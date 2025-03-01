import os
from time import sleep

import pandas as pd
import pytest

from dask_sql import Context
from dask_sql.server.app import _init_app, app
from dask_sql.server.presto_jdbc import create_meta_data

# needed for the testclient
pytest.importorskip("requests")

schema = "a_schema"
table = "a_table"


@pytest.fixture(scope="module")
def c():
    c = Context()
    c.create_schema(schema)
    row = create_table_row()
    tables = pd.DataFrame().append(row, ignore_index=True)
    tables = tables.astype({"AN_INT": "int64"})
    c.create_table(table, tables, schema_name=schema)

    yield c

    c.drop_schema(schema)


@pytest.fixture(scope="module")
def app_client(c):
    c.sql("SELECT 1 + 1").compute()
    _init_app(app, c)
    # late import for the importskip
    from fastapi.testclient import TestClient

    yield TestClient(app)

    # don't disconnect the client if using an independent cluster
    if os.getenv("DASK_SQL_TEST_SCHEDULER", None) is None:
        app.client.close()


@pytest.mark.xfail(reason="WIP DataFusion")
def test_jdbc_has_schema(app_client, c):
    create_meta_data(c)

    check_data(app_client)

    response = app_client.post(
        "/v1/statement", data="SELECT * from system.jdbc.schemas"
    )
    assert response.status_code == 200
    result = get_result_or_error(app_client, response)

    assert_result(result, 2, 3)
    assert result["columns"] == [
        {
            "name": "TABLE_CATALOG",
            "type": "varchar",
            "typeSignature": {"rawType": "varchar", "arguments": []},
        },
        {
            "name": "TABLE_SCHEM",
            "type": "varchar",
            "typeSignature": {"rawType": "varchar", "arguments": []},
        },
    ]
    assert result["data"] == [
        ["", "root"],
        ["", "a_schema"],
        ["", "system_jdbc"],
    ]


@pytest.mark.xfail(reason="WIP DataFusion")
def test_jdbc_has_table(app_client, c):
    create_meta_data(c)
    check_data(app_client)

    response = app_client.post("/v1/statement", data="SELECT * from system.jdbc.tables")
    assert response.status_code == 200
    result = get_result_or_error(app_client, response)

    assert_result(result, 10, 4)
    assert result["data"] == [
        ["", "a_schema", "a_table", "", "", "", "", "", "", ""],
        ["", "system_jdbc", "schemas", "", "", "", "", "", "", ""],
        ["", "system_jdbc", "tables", "", "", "", "", "", "", ""],
        ["", "system_jdbc", "columns", "", "", "", "", "", "", ""],
    ]


@pytest.mark.xfail(reason="WIP DataFusion")
def test_jdbc_has_columns(app_client, c):
    create_meta_data(c)
    check_data(app_client)

    response = app_client.post(
        "/v1/statement",
        data=f"SELECT * from system.jdbc.columns where TABLE_NAME = '{table}'",
    )
    assert response.status_code == 200
    client_result = get_result_or_error(app_client, response)

    # ordering of rows isn't consistent between fastapi versions
    context_result = (
        c.sql("SELECT * FROM system_jdbc.columns WHERE TABLE_NAME = 'a_table'")
        .compute()
        .values.tolist()
    )

    assert_result(client_result, 24, 3)
    assert client_result["data"] == context_result


def assert_result(result, col_len, data_len):
    assert "columns" in result
    assert "data" in result
    assert "error" not in result
    assert len(result["columns"]) == col_len
    assert len(result["data"]) == data_len


def create_table_row(a_str: str = "any", an_int: int = 1, a_float: float = 1.1):
    return {
        "A_STR": a_str,
        "AN_INT": an_int,
        "A_FLOAT": a_float,
    }


def check_data(app_client):
    response = app_client.post("/v1/statement", data=f"SELECT * from {schema}.{table}")
    assert response.status_code == 200
    a_table = get_result_or_error(app_client, response)
    assert "columns" in a_table
    assert "data" in a_table
    assert "error" not in a_table


def get_result_or_error(app_client, response):
    result = response.json()

    assert "nextUri" in result
    assert "error" not in result

    status_url = result["nextUri"]
    next_url = status_url

    counter = 0
    while True:
        response = app_client.get(next_url)
        assert response.status_code == 200

        result = response.json()

        if "nextUri" not in result:
            break

        next_url = result["nextUri"]

        counter += 1
        assert counter <= 100

        sleep(0.1)

    return result
