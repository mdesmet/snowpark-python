#!/usr/bin/env python3
#
# Copyright (c) 2012-2025 Snowflake Computing Inc. All rights reserved.
#

import datetime
import importlib.metadata
import logging
import os
import re
import sys
from typing import Dict, List, Optional, Union
from unittest.mock import patch
from textwrap import dedent

import pytest

try:
    import pandas as pd  # noqa: F401

    from snowflake.snowpark.types import PandasSeries

    is_pandas_available = True
except ImportError:
    is_pandas_available = False

from snowflake.snowpark import Session
from snowflake.snowpark._internal.analyzer.analyzer_utils import unquote_if_quoted
from snowflake.snowpark._internal.udf_utils import resolve_imports_and_packages
from snowflake.snowpark._internal.utils import unwrap_stage_location_single_quote
from snowflake.snowpark.dataframe import DataFrame
from snowflake.snowpark.exceptions import (
    SnowparkInvalidObjectNameException,
    SnowparkSQLException,
)
from snowflake.snowpark.functions import (
    cast,
    col,
    current_date,
    date_from_parts,
    iff,
    lit,
    max as max_,
    pow,
    sproc,
    sqrt,
    system_reference,
)
from snowflake.snowpark.row import Row
from snowflake.snowpark.types import (
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

from tests.utils import (
    IS_IN_STORED_PROC,
    IS_NOT_ON_GITHUB,
    TempObjectType,
    TestFiles,
    Utils,
)

pytestmark = [
    pytest.mark.udf,
]

tmp_stage_name = Utils.random_stage_name()


@pytest.fixture(scope="module", autouse=True)
def setup(session, resources_path, local_testing_mode):
    test_files = TestFiles(resources_path)
    if not local_testing_mode:
        Utils.create_stage(session, tmp_stage_name, is_temporary=True)
        session.add_packages("snowflake-snowpark-python")
    Utils.upload_to_stage(
        session, tmp_stage_name, test_files.test_sp_py_file, compress=False
    )


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="Packaging processing is a NOOP in Local Testing",
    run=False,
)
@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Cannot create session in SP",
)
@patch("snowflake.snowpark._internal.udf_utils.VERSION", (999, 9, 9))
@pytest.mark.parametrize(
    "packages,should_fail",
    [
        # Adding package without version pin should always work
        (["snowflake-snowpark-python"], False),
        # Including a future version fails because it doesn't exist on the server
        (["snowflake-snowpark-python==9999.9.9"], True),
        # Auto including the testing version should fail since it's ahead of what the server can support
        ([], True),
        # Auto including the current version via session also fails.
        (None, True),
    ],
)
def test_add_packages_failures(packages, should_fail, db_parameters):
    def return1(session_):
        return session_.sql("select '1'").collect()[0][0]

    with Session.builder.configs(db_parameters).create() as new_session:
        if should_fail:
            with pytest.raises(
                RuntimeError, match="Cannot add package snowflake-snowpark-python"
            ):
                sproc(
                    return1,
                    session=new_session,
                    return_type=StringType(),
                    packages=packages,
                )
        else:
            return1_sproc = sproc(
                return1,
                session=new_session,
                return_type=StringType(),
                packages=packages,
            )
            assert return1_sproc(session=new_session) == "1"


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="Packaging processing is a NOOP in Local Testing",
    run=False,
)
@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Cannot create session in SP",
)
@patch(
    "snowflake.snowpark.stored_procedure.resolve_imports_and_packages",
    wraps=resolve_imports_and_packages,
)
@pytest.mark.parametrize(
    "session_packages,local_packages",
    [
        # Test that sproc package list is updated correctly
        (["pyyaml"], []),
        # Test that session packages are updated correctly
        ([], ["pyyaml"]),
    ],
)
@patch("snowflake.snowpark._internal.udf_utils.VERSION", (999, 9, 9))
def test__do_register_sp_submits_correct_packages(
    patched_resolve, session_packages, local_packages, db_parameters
):
    major, minor, patch = (999, 9, 9)
    this_package = f"snowflake-snowpark-python=={major}.{minor}.{patch}"

    def return1(session_):
        return session_.sql("select '1'").collect()[0][0]

    with Session.builder.configs(db_parameters).create() as new_session:
        # Adding the testing version of the package fails, but the package list should still be correct
        with pytest.raises(
            RuntimeError, match="Cannot add package snowflake-snowpark-python"
        ):
            sproc(
                return1,
                session=new_session,
                return_type=StringType(),
                packages=["pyyaml"],
            )
        assert patched_resolve.called
        assert (
            session_packages + local_packages + [this_package]
            in patched_resolve.call_args[0]
        )


def test_basic_stored_procedure(session, local_testing_mode):
    def return1(session_):
        return session_.create_dataframe([["1"]]).collect()[0][0]

    def plus1(session_, x):
        return (
            session_.create_dataframe([[x]])
            .to_df(["a"])
            .select(col("a") + lit(1))
            .collect()[0][0]
        )

    def add(session_, x, y):
        return (
            session_.create_dataframe([[x, y]])
            .to_df(["a", "b"])
            .select(col("a") + col("b"))
            .collect()[0][0]
        )

    def int2str(session_, x):
        return (
            session_.create_dataframe([[x]])
            .to_df(["a"])
            .select(cast(col("a"), "string"))
            .collect()[0][0]
        )

    return1_sp = sproc(return1, return_type=StringType())
    plus1_sp = sproc(plus1, return_type=IntegerType(), input_types=[IntegerType()])
    add_sp = sproc(
        add, return_type=IntegerType(), input_types=[IntegerType(), IntegerType()]
    )
    int2str_sp = sproc(int2str, return_type=StringType(), input_types=[IntegerType()])

    assert return1_sp() == "1"
    assert plus1_sp(1) == 2
    assert add_sp(4, 6) == 10
    assert int2str_sp(123) == "123"
    assert return1_sp(session=session) == "1"
    assert plus1_sp(1, session=session) == 2
    assert add_sp(4, 6, session=session) == 10
    assert int2str_sp(123, session=session) == "123"

    def sp_pow(session_, x, y):
        return (
            session_.create_dataframe([[x, y]])
            .to_df(["a", "b"])
            .select(pow(col("a"), col("b")))
            .collect()[0][0]
        )

    pow_sp = sproc(
        sp_pow,
        return_type=DoubleType(),
        input_types=[IntegerType(), IntegerType()],
    )
    assert pow_sp(2, 10) == 1024
    assert pow_sp(2, 10, session=session) == 1024


def test_stored_procedure_with_basic_column_datatype(session, local_testing_mode):
    expected_err = Exception if local_testing_mode else SnowparkSQLException

    def plus1(session_, x):
        return x + 1

    plus1_sp = sproc(plus1, return_type=IntegerType(), input_types=[IntegerType()])
    assert plus1_sp(lit(6)) == 7

    with pytest.raises(expected_err) as ex_info:
        plus1_sp(col("a"))
    assert "invalid identifier" in str(ex_info.value)

    with pytest.raises(expected_err) as ex_info:
        plus1_sp(current_date())
    assert "Invalid argument types for function" in str(
        ex_info.value
    ) or "Unexpected type" in str(ex_info.value)

    with pytest.raises(expected_err) as ex_info:
        plus1_sp(lit(""))
    assert "not recognized" in str(ex_info.value) or "Unexpected type" in str(
        ex_info.value
    )


def test_stored_procedure_with_column_datatype(session, local_testing_mode):
    def add(session_, x, y):
        return x + y

    add_sp = sproc(
        add, return_type=IntegerType(), input_types=[IntegerType(), IntegerType()]
    )

    assert add_sp(4, sqrt(lit(36))) == 10

    if not local_testing_mode:
        dt = datetime.date(1992, 12, 14) + datetime.timedelta(days=3)

        def add_date(session_, date, add_days):
            return date + datetime.timedelta(days=add_days)

        add_date_sp = sproc(
            add_date, return_type=DateType(), input_types=[DateType(), IntegerType()]
        )

        # the date can be different between server and client due to timezone difference
        assert -1 <= (add_date_sp(date_from_parts(1992, 12, 14), 3) - dt).days <= 1


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Named temporary procedure is not supported in stored proc",
)
def test_call_named_stored_procedure(
    session, temp_schema, db_parameters, local_testing_mode
):
    sproc_name = f"test_mul_{Utils.random_alphanumeric_str(3)}"
    if not local_testing_mode:
        session._run_query(f"drop procedure if exists {sproc_name}(int, int)")
    sproc(
        lambda session_, x, y: session_.create_dataframe([[x * y]]).collect()[0][0],
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        name=sproc_name,
    )
    assert session.call(sproc_name, 13, 19) == 13 * 19
    assert (
        session.call(session.get_fully_qualified_name_if_possible(sproc_name), 13, 19)
        == 13 * 19
    )
    if not local_testing_mode:
        # create a stored procedure when the session doesn't have a schema
        new_session = (
            Session.builder.configs(db_parameters)._remove_config("schema").create()
        )
        new_session.sql_simplifier_enabled = session.sql_simplifier_enabled
        new_session.add_packages("snowflake-snowpark-python")
        try:
            assert not new_session.get_current_schema()
            tmp_stage_name_in_temp_schema = f"{temp_schema}.{Utils.random_name_for_temp_object(TempObjectType.STAGE)}"
            new_session._run_query(f"create temp stage {tmp_stage_name_in_temp_schema}")
            full_sp_name = f"{temp_schema}.test_add"
            new_session._run_query(f"drop procedure if exists {full_sp_name}(int, int)")
            new_session.sproc.register(
                lambda session_, x, y: session_.sql(f"select {x} + {y}").collect()[0][
                    0
                ],
                return_type=IntegerType(),
                input_types=[IntegerType(), IntegerType()],
                name=[*temp_schema.split("."), "test_add"],
                stage_location=unwrap_stage_location_single_quote(
                    tmp_stage_name_in_temp_schema
                ),
                is_permanent=True,
            )
            assert new_session.call(full_sp_name, 13, 19) == 13 + 19
            # oen result in the temp schema
            assert (
                len(
                    new_session.sql(
                        f"show procedures like '%test_add%' in schema {temp_schema}"
                    ).collect()
                )
                == 1
            )
        finally:
            new_session.close()
            # restore active session


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="system functions not supported by local testing",
)
def test_infer_table_type_is_skipped_for_system_procedures(session):
    with session.query_history() as history:
        session.call("system$wait", 1)

    assert len(history.queries) == 1


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="system functions not supported by local testing",
)
def test_sproc_pass_system_reference(session, validate_ast):
    table_name = Utils.random_name_for_temp_object(TempObjectType.TABLE)
    df = session.create_dataframe([(1,)]).to_df(["a"])
    df.write.save_as_table(
        table_name,
        mode="ignore" if validate_ast else "errorifexists",
    )

    def insert_and_return_count(session_: Session, table_name_: str) -> int:
        session_.sql(f"INSERT INTO {table_name_} VALUES (2)").collect()
        return session_.table(table_name_).count()

    insert_sproc = sproc(insert_and_return_count, return_type=IntegerType())

    try:
        assert (
            insert_sproc(
                system_reference(
                    "TABLE",
                    table_name,
                    "SESSION",
                    ["SELECT", "INSERT", "UPDATE", "TRUNCATE"],
                )
            )
            == 2
        )
        Utils.check_answer(session.table(table_name), [Row(1), Row(2)])
    finally:
        Utils.drop_table(session, table_name)


@pytest.mark.parametrize("anonymous", [True, False])
def test_call_table_sproc_triggers_action(session, anonymous):
    """Here we create a table sproc which creates a table. we call the table sproc using
    session.call trigger this action and test using session.table that the table was
    indeed created
    """
    sproc_name = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    table_name = Utils.random_name_for_temp_object(TempObjectType.TABLE)

    def create_temp_table_sp(session_: Session, name: str):
        df = session_.create_dataframe([1]).to_df("A")
        df.write.save_as_table(name, mode="overwrite")
        return df

    session.sproc.register(
        create_temp_table_sp,
        name=sproc_name,
        return_type=StructType(),
        input_types=[StringType()],
        replace=True,
        anomymous=anonymous,
    )
    try:
        session.call(sproc_name, table_name)
        Utils.check_answer(session.table(table_name), [Row(A=1)])
    finally:
        Utils.drop_table(session, table_name)


def test_recursive_function(session):
    # Test recursive function
    def factorial(session_, n):
        return 1 if n == 1 or n == 0 else n * factorial(session_, n - 1)

    factorial_sp = sproc(
        factorial, return_type=IntegerType(), input_types=[IntegerType()]
    )
    assert factorial_sp(3) == factorial(session, 3)


def test_nested_function(session):
    def outer_func(session_):
        def inner_func():
            return "snow"

        return session_.create_dataframe([f"{inner_func()}-{inner_func()}"]).collect()[
            0
        ][0]

    def square(session_, x):
        df = session_.create_dataframe([x]).to_df("a")
        return df.select(pow("a", lit(2))).collect()[0][0]

    def cube(session_, x):
        return square(session_, x) * x

    outer_func_sp = sproc(outer_func, return_type=StringType())
    assert outer_func_sp() == "snow-snow"

    # we don't need to register function square()
    cube_sp = sproc(cube, return_type=IntegerType(), input_types=[IntegerType()])
    assert cube_sp(2) == 8

    # but we can still register function square()
    square_sp = sproc(square, return_type=IntegerType(), input_types=[IntegerType()])
    assert cube_sp(2) == 8
    assert square_sp(2) == 4


def test_decorator_function(session):
    def decorator_do_twice(func):
        def wrapper(*args, **kwargs):
            l1 = func(*args, **kwargs)
            l2 = func(*args, **kwargs)
            return l1 * l2

        return wrapper

    @decorator_do_twice
    def square(session_, x):
        df = session_.create_dataframe([x]).to_df("a")
        return df.select(pow("a", lit(2))).collect()[0][0]

    square_twice_sp = sproc(
        square,
        return_type=IntegerType(),
        input_types=[IntegerType()],
    )
    assert square_twice_sp(2) == 16


def test_annotation_syntax(session):
    @sproc(return_type=IntegerType(), input_types=[IntegerType(), IntegerType()])
    def add_sp(session_, x, y):
        df = session_.create_dataframe([(x, y)]).to_df("a", "b")
        return df.select(col("a") + col("b")).collect()[0][0]

    @sproc(return_type=StringType())
    def snow(session_):
        return session_.create_dataframe(["snow"]).collect()[0][0]

    assert add_sp(1, 2) == 3
    assert snow() == "snow"


def test_register_sp_from_file(session, resources_path, tmpdir):
    test_files = TestFiles(resources_path)

    mod5_sp = session.sproc.register_from_file(
        test_files.test_sp_py_file,
        "mod5",
        return_type=IntegerType(),
        input_types=[IntegerType()],
    )
    assert isinstance(mod5_sp.func, tuple)
    assert mod5_sp(3) == 3

    # test zip file
    from zipfile import ZipFile

    zip_path = f"{tmpdir.join(os.path.basename(test_files.test_sp_py_file))}.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.write(
            test_files.test_sp_py_file, os.path.basename(test_files.test_sp_py_file)
        )

    mod5_sp_zip = session.sproc.register_from_file(
        zip_path, "mod5", return_type=IntegerType(), input_types=[IntegerType()]
    )
    assert mod5_sp_zip(3) == 3

    # test a remote python file
    stage_file = f"@{tmp_stage_name}/{os.path.basename(test_files.test_sp_py_file)}"
    mod5_sp_stage = session.sproc.register_from_file(
        stage_file, "mod5", return_type=IntegerType(), input_types=[IntegerType()]
    )
    assert mod5_sp_stage(3) == 3

    # test a table sproc file with type hints
    range5_sproc = session.sproc.register_from_file(
        test_files.test_table_sp_py_file,
        "range5_sproc",
    )
    Utils.check_answer(
        range5_sproc(), [Row(ID=0), Row(ID=1), Row(ID=2), Row(ID=3), Row(ID=4)]
    )


def test_session_register_sp(session, local_testing_mode):
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.create_dataframe([(x, y)])
        .to_df("a", "b")
        .select(col("a") + col("b"))
        .collect()[0][0],
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
    )
    assert add_sp(1, 2) == 3

    query_tag = f"QUERY_TAG_{Utils.random_alphanumeric_str(10)}"
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.create_dataframe([(x, y)])
        .to_df("a", "b")
        .select(col("a") + col("b"))
        .collect()[0][0],
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        statement_params={"QUERY_TAG": query_tag},
    )
    assert add_sp(1, 2) == 3
    Utils.assert_executed_with_query_tag(session, query_tag, local_testing_mode)


def test_add_import_local_file(session, resources_path):
    test_files = TestFiles(resources_path)

    def plus4_then_mod5(session_, x):
        from test_sp_dir.test_sp_file import mod5

        return mod5(
            session_,
            session_.create_dataframe([[x]], schema=["a"])
            .select(col("a") + 4)
            .collect()[0][0],
        )

    def plus4_then_mod5_direct_import(session_, x):
        from test_sp_file import mod5

        return mod5(
            session_,
            session_.create_dataframe([[x]], schema=["a"])
            .select(col("a") + 4)
            .collect()[0][0],
        )

    session.add_import(
        test_files.test_sp_py_file, import_path="test_sp_dir.test_sp_file"
    )
    plus4_then_mod5_sp = sproc(
        plus4_then_mod5, return_type=IntegerType(), input_types=[IntegerType()]
    )

    assert plus4_then_mod5_sp(3) == 2

    # if import_as argument changes, the checksum of the file will also change
    # and we will overwrite the file in the stage
    session.add_import(test_files.test_sp_py_file)
    plus4_then_mod5_direct_import_sp = sproc(
        plus4_then_mod5_direct_import,
        return_type=IntegerType(),
        input_types=[IntegerType()],
    )
    assert plus4_then_mod5_direct_import_sp(3) == 2

    # clean
    session.clear_imports()


def test_add_import_local_directory(session, resources_path):
    test_files = TestFiles(resources_path)

    def plus4_then_mod5(session_, x):
        from resources.test_sp_dir.test_sp_file import mod5

        return mod5(
            session_,
            session_.create_dataframe([[x]], schema=["a"])
            .select(col("a") + 4)
            .collect()[0][0],
        )

    def plus4_then_mod5_direct_import(session_, x):
        from test_sp_dir.test_sp_file import mod5

        return mod5(
            session_,
            session_.create_dataframe([[x]], schema=["a"])
            .select(col("a") + 4)
            .collect()[0][0],
        )

    session.add_import(
        test_files.test_sp_directory, import_path="resources.test_sp_dir"
    )
    plus4_then_mod5_sp = sproc(
        plus4_then_mod5, return_type=IntegerType(), input_types=[IntegerType()]
    )
    assert plus4_then_mod5_sp(3) == 2

    session.add_import(test_files.test_sp_directory)
    plus4_then_mod5_direct_import_sp = sproc(
        plus4_then_mod5_direct_import,
        return_type=IntegerType(),
        input_types=[IntegerType()],
    )
    assert plus4_then_mod5_direct_import_sp(3) == 2

    # clean
    session.clear_imports()


def test_add_import_stage_file(session, resources_path):
    test_files = TestFiles(resources_path)

    def plus4_then_mod5(session_, x):
        from test_sp_file import mod5

        return mod5(
            session_,
            session_.create_dataframe([[x]], schema=["a"])
            .select(col("a") + 4)
            .collect()[0][0],
        )

    stage_file = f"@{tmp_stage_name}/{os.path.basename(test_files.test_sp_py_file)}"
    session.add_import(stage_file)
    plus4_then_mod5_sp = sproc(
        plus4_then_mod5, return_type=IntegerType(), input_types=[IntegerType()]
    )

    assert plus4_then_mod5_sp(3) == 2

    # clean
    session.clear_imports()


def test_sp_level_import(session, resources_path, local_testing_mode):
    test_files = TestFiles(resources_path)

    def plus4_then_mod5(session_, x):
        from test_sp_dir.test_sp_file import mod5

        return mod5(
            session_,
            session_.create_dataframe([[x]], schema=["a"])
            .select(col("a") + 4)
            .collect()[0][0],
        )

    # with sp-level imports
    plus4_then_mod5_sp = sproc(
        plus4_then_mod5,
        return_type=IntegerType(),
        input_types=[IntegerType()],
        imports=[(test_files.test_sp_py_file, "test_sp_dir.test_sp_file")],
    )
    assert plus4_then_mod5_sp(3) == 2

    # without sp-level imports
    plus4_then_mod5_sp = sproc(
        plus4_then_mod5,
        return_type=IntegerType(),
        input_types=[IntegerType()],
    )

    with pytest.raises(SnowparkSQLException) as ex_info:
        plus4_then_mod5_sp(3)

    if local_testing_mode:
        # Local testing nests the error, but pytest only provides the top level error message
        assert "Python Interpreter Error" in ex_info.value.message
    else:
        assert "No module named" in ex_info.value.message


def test_type_hints(session):
    @sproc()
    def add_sp(session_: Session, x: int, y: int) -> int:
        df = session_.create_dataframe(
            [
                (x, y),
            ]
        ).to_df(["a", "b"])
        return df.select(col("a") + col("b")).collect()[0][0]

    @sproc
    def snow_sp(session_: Session, x: int) -> Optional[str]:
        df = session_.create_dataframe(
            [
                (x),
            ]
        ).to_df(["a"])
        return df.select(iff(col("a") % 2 == 0, "snow", None)).collect()[0][0]

    @sproc
    def double_str_list_sp(session_: Session, x: str) -> List[str]:
        df = session_.create_dataframe(
            [
                (x),
            ]
        ).to_df(["a"])
        val = df.collect()[0][0]
        return [val, val]

    dt = datetime.datetime.strptime("2017-02-24 12:00:05.456", "%Y-%m-%d %H:%M:%S.%f")

    @sproc
    def return_datetime_sp(_: Session) -> datetime.datetime:
        return dt

    @sproc
    def first_element_sp(_: Session, x: List[str]) -> str:
        return x[0]

    @sproc
    def get_sp(_: Session, d: Dict[str, str], i: str) -> str:
        return d[i]

    assert add_sp(1, 2) == 3
    assert snow_sp(1) is None
    assert snow_sp(2) == "snow"
    assert double_str_list_sp("abc") == '[\n  "abc",\n  "abc"\n]'
    assert return_datetime_sp() == dt
    assert first_element_sp(["0", "'"]) == "0"
    assert get_sp({"0": "snow", "1": "flake"}, "0") == "snow"


def test_type_hint_no_change_after_registration(session):
    def add(session_: Session, x: int, y: int) -> int:
        return (
            session_.create_dataframe([(x, y)])
            .to_df("a", "b")
            .select(col("a") + col("b"))
            .collect()[0][0],
        )

    annotations = add.__annotations__
    session.sproc.register(add)
    assert annotations == add.__annotations__


def test_register_sp_from_file_type_hints(session, tmpdir):
    source = """
import datetime
import snowflake
from snowflake.snowpark import Session
from typing import Dict, List, Optional
from snowflake.snowpark.functions import (
    col,
    iff,
    lit
)

def add(session: snowflake.snowpark.Session, x: int, y: int) -> int:
    return session.create_dataframe([[x, y]], schema=["x", "y"]).select(col("x")+col("y")).collect()[0][0]

def snow(session_: Session, x: int) -> Optional[str]:
    return session_.create_dataframe([[x]],schema=["x"]).select(iff(col("x")%2==0, lit('snow'), lit(None))).collect()[0][0]

def double_str_list(session_: snowflake.snowpark.Session, x: str) -> List[str]:
    val = session_.create_dataframe([[str(x)]]).collect()[0][0]
    return [val, val]

dt = datetime.datetime.strptime("2017-02-24 12:00:05.456", "%Y-%m-%d %H:%M:%S.%f")

def return_datetime(_: Session) -> datetime.datetime:
    return dt
"""
    file_path = os.path.join(tmpdir, "register_from_file_type_hints.py")
    with open(file_path, "w") as f:
        f.write(source)

    add_sp = session.sproc.register_from_file(file_path, "add")
    add_sp_with_statement_params = session.sproc.register_from_file(
        file_path, "add", statement_params={"SF_PARTNER": "FAKE_PARTNER"}
    )
    snow_sp = session.sproc.register_from_file(file_path, "snow")
    double_str_list_sp = session.sproc.register_from_file(file_path, "double_str_list")
    return_datetime_sp = session.sproc.register_from_file(file_path, "return_datetime")

    assert add_sp(1, 2) == 3
    assert add_sp_with_statement_params(1, 2) == 3
    assert snow_sp(0) == "snow"
    assert snow_sp(1) is None
    assert double_str_list_sp("abc") == '[\n  "abc",\n  "abc"\n]'

    dt = datetime.datetime.strptime("2017-02-24 12:00:05.456", "%Y-%m-%d %H:%M:%S.%f")
    assert return_datetime_sp() == dt


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="SNOW-1412530 to fix bug",
    run=False,
)
@pytest.mark.parametrize("register_from_file", [True, False])
def test_register_sp_with_optional_args(session: Session, tmpdir, register_from_file):
    import decimal  # noqa: F401
    from snowflake.snowpark.types import Variant, Geometry, Geography  # noqa: F401

    import_body = """
import datetime
import decimal
from snowflake.snowpark import Session
from snowflake.snowpark.types import Variant, Geometry, Geography
from snowflake.snowpark.functions import (
    col,
    iff,
    lit
)
from typing import Dict, List, Optional
"""
    func_body = """
def add(session_: Session, x: int = 0, y: int = 0) -> int:
    return (
        session_.create_dataframe([[x, y]], schema=["x", "y"])
        .select(col("x") + col("y"))
        .collect()[0][0]
    )

def snow(session_: Session, x: int = 1) -> Optional[str]:
    return (
        session_.create_dataframe([[x]], schema=["x"])
        .select(iff(col("x") % 2 == 0, lit("snow"), lit(None)))
        .collect()[0][0]
    )

def double_str_list(session_: Session, x: str = "a") -> List[str]:
    val = session_.create_dataframe([[str(x)]]).collect()[0][0]
    return [val, val]

def return_date(
    _: Session, dt: datetime.date = datetime.date(2017, 1, 1)
) -> datetime.date:
    return dt

def return_arr(
    _: Session, base_arr: List[int], extra_arr: List[int] = [4]
) -> List[int]:
    base_arr.extend(extra_arr)
    return base_arr

def return_all_datatypes(
    _: Session,
    a: int = 1,
    b: float = 1.0,
    c: str = "one",
    d: List[int] = [],
    e: Dict[str, int] = {"s": 1},
    f: Variant = {"key": "val"},
    g: Geometry = "POINT(-122.35 37.55)",
    h: Geography = "POINT(-122.35 37.55)",
    i: datetime.datetime = datetime.datetime(2021, 1, 1, 0, 0, 0),
    j: datetime.date = datetime.date(2021, 1, 1),
    k: datetime.time = datetime.time(0, 0, 0),
    l: bytes = b"123",
    m: bool = True,
    n: decimal.Decimal = decimal.Decimal(1.0),
) -> str:
    final_str = f"{a}, {b}, {c}, {d}, {e}, {f}, {g}, {h}, {i}, {j}, {k}, {l}, {m}, {n}"
    return final_str
"""
    if register_from_file:
        file_path = os.path.join(tmpdir, "register_from_file_optional_args.py")
        with open(file_path, "w") as f:
            source = f"{import_body}\n{func_body}"
            f.write(source)

        add_sp = session.sproc.register_from_file(file_path, "add")
        snow_sp = session.sproc.register_from_file(file_path, "snow")
        double_str_list_sp = session.sproc.register_from_file(
            file_path, "double_str_list"
        )
        return_date_sp = session.sproc.register_from_file(file_path, "return_date")
        return_arr_sp = session.sproc.register_from_file(file_path, "return_arr")
        return_all_types_sp = session.sproc.register_from_file(
            file_path, "return_all_datatypes"
        )
    else:
        d = {}
        exec(func_body, {**globals(), **locals()}, d)

        add_sp = session.sproc.register(d["add"])
        snow_sp = session.sproc.register(d["snow"])
        double_str_list_sp = session.sproc.register(d["double_str_list"])
        return_date_sp = session.sproc.register(d["return_date"])
        return_arr_sp = session.sproc.register(d["return_arr"])
        return_all_types_sp = session.sproc.register(d["return_all_datatypes"])

    assert add_sp(1, 2) == 3
    assert add_sp(1) == 1
    assert add_sp() == 0
    assert snow_sp(0) == "snow"
    assert snow_sp(1) is None
    assert snow_sp() is None
    assert double_str_list_sp("abc") == '[\n  "abc",\n  "abc"\n]'
    assert double_str_list_sp() == '[\n  "a",\n  "a"\n]'
    assert return_date_sp(datetime.date(2024, 1, 2)) == datetime.date(2024, 1, 2)
    assert return_date_sp() == datetime.date(2017, 1, 1)
    assert return_arr_sp([1, 2, 3], [4, 5]) == "[\n  1,\n  2,\n  3,\n  4,\n  5\n]"
    assert return_arr_sp([1, 2, 3]) == "[\n  1,\n  2,\n  3,\n  4\n]"
    assert return_all_types_sp() == (
        "1, 1.0, one, [], {'s': 1}, {'key': 'val'}, {'coordinates': [-122.35, 37.55], 'type': 'Point'}, "
        "{'coordinates': [-122.35, 37.55], 'type': 'Point'}, 2021-01-01 00:00:00, 2021-01-01, 00:00:00, "
        "b'123', True, 1.000000000000000000"
    )
    assert return_all_types_sp(2, 2.0, "two", [1, 2, 3]) == (
        "2, 2.0, two, [1, 2, 3], {'s': 1}, {'key': 'val'}, {'coordinates': [-122.35, 37.55], 'type': 'Point'}, "
        "{'coordinates': [-122.35, 37.55], 'type': 'Point'}, 2021-01-01 00:00:00, 2021-01-01, 00:00:00, "
        "b'123', True, 1.000000000000000000"
    )


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="Database objects do not persist across sessions in Local Testing",
    run=False,
)
@pytest.mark.skipif(IS_IN_STORED_PROC, reason="Cannot create session in SP")
def test_permanent_sp(session, db_parameters):
    stage_name = Utils.random_stage_name()
    sp_name = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    with Session.builder.configs(db_parameters).create() as new_session:
        new_session.sql_simplifier_enabled = session.sql_simplifier_enabled
        new_session.add_packages("snowflake-snowpark-python")
        try:
            Utils.create_stage(session, stage_name, is_temporary=False)
            sproc(
                lambda session_, x, y: session_.sql(f"SELECT {x} + {y}").collect()[0][
                    0
                ],
                return_type=IntegerType(),
                input_types=[IntegerType(), IntegerType()],
                name=sp_name,
                is_permanent=True,
                stage_location=stage_name,
                session=new_session,
            )

            assert session.call(sp_name, 1, 2) == 3
            assert new_session.call(sp_name, 8, 9) == 17
        finally:
            session._run_query(f"drop function if exists {sp_name}(int, int)")
            Utils.drop_stage(session, stage_name)


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="Database objects do not persist across sessions in Local Testing",
    run=False,
)
@pytest.mark.skipif(IS_IN_STORED_PROC, reason="Cannot create session in SP")
def test_permanent_sp_negative(session, db_parameters):
    stage_name = Utils.random_stage_name()
    sp_name = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    with Session.builder.configs(db_parameters).create() as new_session:
        new_session.sql_simplifier_enabled = session.sql_simplifier_enabled
        new_session.add_packages("snowflake-snowpark-python")
        try:
            Utils.create_stage(session, stage_name, is_temporary=False)
            sproc(
                lambda session_, x, y: session_.sql(f"SELECT {x} + {y}").collect()[0][
                    0
                ],
                return_type=IntegerType(),
                input_types=[IntegerType(), IntegerType()],
                name=sp_name,
                is_permanent=False,
                stage_location=stage_name,
                session=new_session,
            )

            with pytest.raises(
                SnowparkSQLException, match=f"Unknown function {sp_name}"
            ):
                session.call(sp_name, 1, 2)
            assert new_session.call(sp_name, 8, 9) == 17
        finally:
            new_session._run_query(f"drop function if exists {sp_name}(int, int)")
            Utils.drop_stage(session, stage_name)


@pytest.mark.skipif(not is_pandas_available, reason="Requires pandas")
def test_sp_negative(session, local_testing_mode):
    def f(_, x):
        return x

    empty_sp = sproc()
    with pytest.raises(TypeError) as ex_info:
        empty_sp(session)
    assert "Invalid function: not a function or callable" in str(ex_info)

    with pytest.raises(TypeError) as ex_info:
        sproc(1, return_type=IntegerType())
    assert "Invalid function: not a function or callable" in str(ex_info)

    # if return_type is specified, it must be passed with keyword argument
    with pytest.raises(
        TypeError,
        match=re.escape(
            "sproc() takes from 0 to 1 positional arguments but 2"
            " positional arguments (and 1 keyword-only argument) were given"
        ),
    ):
        sproc(f, IntegerType())

    f_sp = sproc(f, return_type=IntegerType(), input_types=[IntegerType()])
    with pytest.raises(ValueError) as ex_info:
        f_sp("a", "")
    assert "Incorrect number of arguments passed to the stored procedure" in str(
        ex_info
    )

    with pytest.raises(SnowparkSQLException) as ex_info:
        session.call("f", 1).collect()

    assert "Unknown function" in str(ex_info.value)

    with pytest.raises(SnowparkInvalidObjectNameException) as ex_info:
        sproc(
            f,
            return_type=IntegerType(),
            input_types=[IntegerType()],
            name="invalid name",
        )
    assert "The object name 'invalid name' is invalid" in str(ex_info.value)

    # incorrect data type
    int_sp = sproc(
        lambda _, x: int(x), return_type=IntegerType(), input_types=[IntegerType()]
    )
    with pytest.raises(SnowparkSQLException) as ex_info:
        int_sp("x")
    assert "is not recognized" in str(ex_info.value) or "Unexpected type" in str(
        ex_info.value
    )

    with pytest.raises(SnowparkSQLException) as ex_info:
        int_sp(None)
    assert "Python Interpreter Error" in str(ex_info.value)

    with pytest.raises(TypeError) as ex_info:

        @sproc(IntegerType())
        def g(_, x):
            return x

    assert "Invalid function: not a function or callable" in str(ex_info)

    with pytest.raises(TypeError) as ex_info:

        @sproc
        def _(_: Session, x: int, y: int):
            return x + y

    assert "The return type must be specified" in str(ex_info)

    with pytest.raises(TypeError) as ex_info:

        @sproc
        def _(_: Session, x, y: int) -> int:
            return x + y

    assert (
        "Excluding session argument in stored procedure, "
        "the number of arguments (2) is different from "
        "the number of argument type hints (1)" in str(ex_info)
    )

    with pytest.raises(TypeError) as ex_info:

        @sproc
        def _(_: Session, x: int, y: Union[int, float]) -> Union[int, float]:
            return x + y

    assert "invalid type typing.Union[int, float]" in str(ex_info)

    with pytest.raises(TypeError) as ex_info:

        @sproc
        def _(_: int, x: int, y: int) -> int:
            return x + y

    assert "The first argument of stored proc function should be Session" in str(
        ex_info
    )

    with pytest.raises(ValueError) as ex_info:

        @sproc(is_permanent=True)
        def _(_: Session, x: int, y: int) -> int:
            return x + y

    assert "name must be specified for permanent stored proc" in str(ex_info)

    with pytest.raises(ValueError) as ex_info:

        @sproc(is_permanent=True, name="sp")
        def _(_: Session, x: int, y: int) -> int:
            return x + y

    assert "stage_location must be specified for permanent stored proc" in str(ex_info)

    with pytest.raises(TypeError) as ex_info:

        @sproc
        def _(
            _: Session, x: PandasSeries[int], y: PandasSeries[int]
        ) -> PandasSeries[int]:
            return x + y

    assert "pandas stored procedure is not supported" in str(ex_info)


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="Table sproc is not supported in Local Testing",
)
@pytest.mark.parametrize("is_permanent", [True, False])
@pytest.mark.parametrize("anonymous", [True, False])
@pytest.mark.parametrize(
    "ret_type",
    [
        StructType(),
        StructType(
            [
                StructField("a", StringType()),
                StructField("b", StringType()),
                StructField("c", StringType()),
            ]
        ),
    ],
)
@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Named temporary procedure is not supported in stored proc",
)
def test_table_sproc(session, is_permanent, anonymous, ret_type):
    """Ensure the following scenarios work:
    - register sproc with session.sproc.register
    - register sproc with @sproc decorator
    - can create permanent and temporary sprocs
    - can create anonymous sprocs
    - session.call works with provided function name
    - we can both specify return cols types and keep it blank
        - exception: sproc from decorator and implicit type hint cannot specify return col types
    - dataframe returned after a sproc call can be operated on like normal dataframes
    """
    if len(ret_type.fields) == 0 and not session.sql_simplifier_enabled:
        # if return type does not define output columns and sql_simplifier is
        # disabled, then we don't support dataframe operations on table sprocs
        pytest.skip()

    tmp_table_name = Utils.random_name_for_temp_object(TempObjectType.TABLE)
    Utils.create_table(session, tmp_table_name, "a String, b String, c Date")
    table_df = session.create_dataframe(
        [
            ["sqlite", "3.41.1", "2023-03-15"],
            ["sqlite", "3.32.3", "2023-01-25"],
            ["jsonschema", "4.4.0", "2023-05-06"],
            ["jsonschema", "3.2.0", "2022-12-09"],
            ["zope", "1.0", "2020-01-01"],
            ["flake8", "4.0.1", "2022-11-11"],
            ["flake8", "3.9.2", "2022-08-22"],
            ["flake8", "6.0.0", "2023-02-12"],
        ],
        schema=["a", "b", "c"],
    )
    table_df.write.save_as_table(tmp_table_name, mode="overwrite")

    stage_name = Utils.random_stage_name()
    Utils.create_stage(session, stage_name, is_temporary=False)

    # in all tests below, we select * from tmp_table created above. Then on that DataFrame, we apply
    # group_by("a") and aggregate the max("b") as column "max_b". For all these, below is the expected output
    expected = [
        Row(A="flake8", MAX_B="6.0.0"),
        Row(A="jsonschema", MAX_B="4.4.0"),
        Row(A="sqlite", MAX_B="3.41.1"),
        Row(A="zope", MAX_B="1.0"),
    ]

    temp_sp_name_register = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    temp_sp_name_decorator = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    try:
        # tests with session.sproc.register
        select_star_register_sp = session.sproc.register(
            lambda session_, name: session_.sql(f"SELECT * from {name}"),
            name=temp_sp_name_register,
            return_type=ret_type,
            input_types=[StringType()],
            replace=True,
            is_permanent=is_permanent,
            stage_location=stage_name,
            anonymous=anonymous,
        )

        df = select_star_register_sp(tmp_table_name)
        df = df.select("a", "b").group_by("a").agg(max_("b").as_("max_b"))
        Utils.check_answer(df, expected)

        # tests with @sproc decorator
        @sproc(
            name=temp_sp_name_decorator,
            replace=True,
            return_type=ret_type if len(ret_type.fields) > 0 else None,
            anonymous=anonymous,
            is_permanent=is_permanent,
            stage_location=stage_name,
        )
        def select_star_decorator_sp(session_: Session, name: str) -> DataFrame:
            return session_.sql(f"select * from {name}")

        df = select_star_decorator_sp(tmp_table_name)
        df = df.select("a", "b").group_by("a").agg(max_("b").as_("max_b"))
        Utils.check_answer(df, expected)

        if not anonymous:
            # session.call test for sproc.register
            df = session.call(temp_sp_name_register, tmp_table_name)
            df = df.select("a", "b").group_by("a").agg(max_("b").as_("max_b"))
            Utils.check_answer(df, expected)

            # session.call test for decorator
            df = session.call(temp_sp_name_decorator, tmp_table_name)
            df = df.select("a", "b").group_by("a").agg(max_("b").as_("max_b"))
            Utils.check_answer(df, expected)
    finally:
        session._run_query(f"drop procedure if exists {temp_sp_name_register}(string)")
        session._run_query(f"drop procedure if exists {temp_sp_name_decorator}(string)")
        Utils.drop_stage(session, stage_name)


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="SNOW-952138 Table sproc is not supported in Local Testing",
)
def test_table_sproc_negative(session, caplog):
    temp_sp_name1 = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    temp_sp_name2 = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    try:
        session.sproc.register(
            lambda session_, name: session_.sql(f"SELECT * from {name}"),
            name=temp_sp_name1,
            return_type=StructType(),
            input_types=[StringType()],
            replace=True,
        )

        # we log warning when table signature does not match
        with pytest.raises(
            SnowparkSQLException, match=f"unexpected '35'. in function {temp_sp_name1}"
        ):
            with caplog.at_level(logging.INFO):
                session.call(temp_sp_name1, 35, log_on_exception=True)
        assert f"Could not describe procedure {temp_sp_name1}(BIGINT)" in caplog.text

        @sproc(name=temp_sp_name2, session=session)
        def hello_sp(session: Session, name: str, age: int) -> str:
            if age is None:
                age = 28
            return f"Hello {name} with age {age}"

        caplog.clear()
        with caplog.at_level(logging.WARN):
            session.call(temp_sp_name2, "al'Thor", None, log_on_exception=True)
        assert f"{temp_sp_name2}' does not exist or not authorized" in caplog.text

        caplog.clear()
        with caplog.at_level(logging.WARN):
            session.call(temp_sp_name2, "al'Thor", None, log_on_exception=False)
        assert f"{temp_sp_name2}' does not exist or not authorized" not in caplog.text
    finally:
        session._run_query(f"drop procedure if exists {temp_sp_name1}(string)")
        session._run_query(f"drop procedure if exists {temp_sp_name2}(string, bigint)")


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="SNOW-952138 Table sproc is not supported in Local Testing",
)
def test_table_sproc_with_type_none_argument(session):
    temp_sp_name = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    try:

        @sproc(name=temp_sp_name, session=session)
        def hello_sp(session: Session, name: str, age: int) -> DataFrame:
            if age is None:
                age = 100
            return session.sql(f"select '{name}' as name, {age} as age")

        Utils.check_answer(
            session.call(temp_sp_name, "afroz", 26), [Row(NAME="afroz", AGE=26)]
        )
        Utils.check_answer(
            session.call(temp_sp_name, "afroz", lit(26)), [Row(NAME="afroz", AGE=26)]
        )
        Utils.check_answer(
            session.call(temp_sp_name, "Joe", lit(None).cast(IntegerType())),
            [Row(NAME="Joe", AGE=100)],
        )
    finally:
        Utils.drop_procedure(session, f"{temp_sp_name}(string, bigint)")


def test_temp_sp_with_import_and_upload_stage(
    session, resources_path, local_testing_mode
):
    """We want temporary stored procs to be able to do the following:
    - Do not upload packages to permanent stage locations
    - Can import packages from permanent stage locations
    - Can upload packages to temp stages for custom usage
    - Import from permanent stage location and upload to temp stage + import from temp stage should
    work
    """
    stage_name = Utils.random_stage_name()
    if not local_testing_mode:
        Utils.create_stage(session, stage_name, is_temporary=False)
    test_files = TestFiles(resources_path)
    # upload test_sp_dir.test_sp_file (mod5) to permanent stage and use mod3
    # file for temporary stage import correctness
    if local_testing_mode:
        session.file.put(
            test_files.test_sp_py_file,
            unwrap_stage_location_single_quote(stage_name),
            auto_compress=False,
            overwrite=True,
        )
    else:
        session._conn.upload_file(
            path=test_files.test_sp_py_file,
            stage_location=unwrap_stage_location_single_quote(stage_name),
            compress_data=False,
            overwrite=True,
            skip_upload_on_content_match=True,
        )
    try:
        # Can import packages from permanent stage locations
        def mod5_(session_, x):
            from test_sp_file import mod5

            return mod5(session_, x)

        mod5_sproc = sproc(
            mod5_,
            return_type=IntegerType(),
            input_types=[IntegerType()],
            imports=[f"@{stage_name}/test_sp_file.py"],
            is_permanent=False,
        )
        assert mod5_sproc(5) == 0

        # Can upload packages to temp stages for custom usage
        def mod3_(session_, x):
            from test_sp_mod3_file import mod3

            return mod3(session_, x)

        mod3_sproc = sproc(
            mod3_,
            return_type=IntegerType(),
            input_types=[IntegerType()],
            imports=[test_files.test_sp_mod3_py_file],
        )

        assert mod3_sproc(3) == 0

        # Import from permanent stage location and upload to temp stage + import
        # from temp stage should work
        def mod3_of_mod5_(session_, x):
            from test_sp_file import mod5
            from test_sp_mod3_file import mod3

            return mod3(session_, mod5(session_, x))

        mod3_of_mod5_sproc = sproc(
            mod3_of_mod5_,
            return_type=IntegerType(),
            input_types=[IntegerType()],
            imports=[f"@{stage_name}/test_sp_file.py", test_files.test_sp_mod3_py_file],
        )

        assert mod3_of_mod5_sproc(4) == 1
    finally:
        if not local_testing_mode:
            Utils.drop_stage(session, stage_name)


def test_add_import_negative(session, resources_path, local_testing_mode):
    test_files = TestFiles(resources_path)

    def plus4_then_mod5(_, x):
        from test.resources.test_sp_dir.test_sp_file import mod5

        return mod5(x + 4)

    for import_path in [
        None,
        "resources.test_sp_dir.test_sp_file",
        "test_sp_dir.test_sp_file",
        "test_sp_file",
    ]:
        session.add_import(test_files.test_sp_py_file, import_path)
        plus4_then_mod5_sp = sproc(
            plus4_then_mod5, return_type=IntegerType(), input_types=[IntegerType()]
        )
        expected_exc = SnowparkSQLException
        with pytest.raises(expected_exc) as ex_info:
            plus4_then_mod5_sp(1)
        assert "No module named 'test.resources'" in str(ex_info.value)
    session.clear_imports()

    with pytest.raises(TypeError) as ex_info:
        sproc(
            plus4_then_mod5,
            return_type=IntegerType(),
            input_types=[IntegerType()],
            imports=[1],
        )
    assert (
        "stored-proc-level import can only be a file path (str) "
        "or a tuple of the file path (str) and the import path (str)" in str(ex_info)
    )


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Named temporary procedure is not supported in stored proc",
)
def test_sp_replace(session):
    # Register named sp and expect that it works.
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.create_dataframe([[x + y]]).collect()[0][0],
        name="test_sp_replace_add",
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        replace=True,
    )
    assert add_sp(1, 2) == 3

    # Replace named sp with different one and expect that data is changed.
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.create_dataframe([[x + y + 1]]).collect()[0][0],
        name="test_sp_replace_add",
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        replace=True,
    )
    assert add_sp(1, 2) == 4

    # Try to register sp without replacing and expect failure.
    with pytest.raises(SnowparkSQLException) as ex_info:
        add_sp = session.sproc.register(
            lambda session_, x, y: session_.sql(f"SELECT {x} + {y}").collect()[0][0],
            name="test_sp_replace_add",
            return_type=IntegerType(),
            input_types=[IntegerType(), IntegerType()],
        )
    assert "SQL compilation error" in str(ex_info.value)

    # Expect second sp version to still be there.
    assert add_sp(1, 2) == 4

    # Register via sproc() in functions.py and expect that it works.
    add_sp = sproc(
        lambda session_, x, y: session_.create_dataframe([[x + y]]).collect()[0][0],
        name="test_sp_replace_add",
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        replace=True,
    )
    assert add_sp(1, 2) == 3


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Named temporary procedure is not supported in stored proc",
)
def test_sp_if_not_exists(session):
    # Register named sp and expect that it works.
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.create_dataframe([[x + y]]).collect()[0][0],
        name="test_sp_if_not_exists_add",
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        if_not_exists=True,
    )
    assert add_sp(1, 2) == 3

    # if_not_exists named sp with different one and expect that data is changed.
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.create_dataframe([[x + y + 1]]).collect()[0][0],
        name="test_sp_if_not_exists_add",
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        if_not_exists=True,
    )
    assert add_sp(1, 2) == 3

    # Try to register sp without if-exists check and expect failure.
    with pytest.raises(SnowparkSQLException, match="already exists"):
        add_sp = session.sproc.register(
            lambda session_, x, y: session_.create_dataframe([[x + y + 1]]).collect()[
                0
            ][0],
            name="test_sp_if_not_exists_add",
            return_type=IntegerType(),
            input_types=[IntegerType(), IntegerType()],
            if_not_exists=False,
        )

    # Try to register sp with replace and if-exists check and expect failure.
    with pytest.raises(
        ValueError,
        match="options replace and if_not_exists are incompatible",
    ):
        add_sp = session.sproc.register(
            lambda session_, x, y: session_.create_dataframe([[x + y + 1]]).collect()[
                0
            ][0],
            name="test_sp_if_not_exists_add",
            return_type=IntegerType(),
            input_types=[IntegerType(), IntegerType()],
            replace=True,
            if_not_exists=True,
        )

    # Expect first sp version to still be there.
    assert add_sp(1, 2) == 3


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="Local Testing doesn't PUT the files, so parallel is trivial",
    run=False,
)
def test_sp_parallel():
    for i in [1, 50, 99]:
        sproc(
            lambda session_, x, y: session_.sql(f"SELECT {x} + {y}").collect()[0][0],
            return_type=IntegerType(),
            input_types=[IntegerType(), IntegerType()],
            parallel=i,
        )

    with pytest.raises(ValueError) as ex_info:
        sproc(
            lambda session_, x, y: session_.sql(f"SELECT {x} + {y}").collect()[0][0],
            return_type=IntegerType(),
            input_types=[IntegerType(), IntegerType()],
            parallel=0,
        )
    assert "Supported values of parallel are from 1 to 99" in str(ex_info)

    with pytest.raises(ValueError) as ex_info:
        sproc(
            lambda session_, x, y: session_.sql(f"SELECT {x} + {y}").collect()[0][0],
            return_type=IntegerType(),
            input_types=[IntegerType(), IntegerType()],
            parallel=100,
        )
    assert "Supported values of parallel are from 1 to 99" in str(ex_info)


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="Comment is a SQL feature",
    run=False,
)
@pytest.mark.parametrize(
    "prefix",
    ["simple", "'single quotes'", '"double quotes"', "\nnew line", "\\backslash"],
)
def test_create_sproc_with_comment(session, prefix):
    suffix = Utils.random_alphanumeric_str(6)
    comment = f"{prefix} {suffix}"

    def return1(session_: Session) -> str:
        return session_.sql("select '1'").collect()[0][0]

    return1_sp = session.sproc.register(return1, comment=comment)

    ddl_sql = f"select get_ddl('PROCEDURE', '{return1_sp.name}()')"
    ddl = session.sql(ddl_sql).collect()[0][0]
    assert "COMMENT=" in ddl
    assert suffix in ddl


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="StoredProcedure.describe is not supported in Local Testing",
)
@pytest.mark.parametrize("source_code_display", [(True,), (False,)])
def test_describe_sp(session, source_code_display):
    def return1(session_: Session) -> str:
        return session_.sql("select '1'").collect()[0][0]

    return1_sp = session.sproc.register(return1)
    describe_res = session.sproc.describe(return1_sp).collect()
    actual_fields = [row[0] for row in describe_res]
    expected_fields = [
        "signature",
        "returns",
        "language",
        "null handling",
        "volatility",
        "execute as",
        "body",
        "imports",
        "handler",
        "runtime_version",
        "packages",
        "installed_packages",
        # This seems like an unintended change from the server, we should remove it once it is removed from server
        "is_aggregate",
    ]
    # We use zip such that it is compatible regardless of UDAF is enabled or not on the merge gate accounts
    for actual_field, expected_field in zip(actual_fields, expected_fields):
        assert (
            actual_field == expected_field
        ), f"Actual: {actual_fields}, Expected: {expected_fields}"

    for row in describe_res:
        if row[0] == "packages":
            assert "snowflake-snowpark-python" in row[1]
        elif row[0] == "body" and source_code_display:
            assert (
                "# The following comment contains the source code generated by snowpark-python for explanatory purposes.\n# def return1(session_: Session) -> str:\n#     return session_.sql(\"select '1'\").collect()[0][0]\n#\n# func = return1\n\ndef compute(session):\n    return func(session)\n"
                in row[1]
            )


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="This is testing SQL feature",
    run=False,
)
def test_register_sp_no_commit(session):
    def plus1(_: Session, x: int) -> int:
        return x + 1

    temp_sp_name = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    perm_sp_name = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)

    try:
        # Test stored proc registration
        session.sql("begin").collect()
        session.sproc.register(func=plus1, name=temp_sp_name)
        assert Utils.is_active_transaction(session)
        session.sproc.register(
            func=plus1, name=perm_sp_name, stage_location=tmp_stage_name
        )
        assert Utils.is_active_transaction(session)

        # Test stored proc call
        assert session.call(temp_sp_name, 1) == 2
        assert session.call(perm_sp_name, 1) == 2
        assert Utils.is_active_transaction(session)

        session.sql("commit").collect()
        assert not Utils.is_active_transaction(session)
    finally:
        session._run_query(f"drop procedure if exists {temp_sp_name}(int)")
        session._run_query(f"drop procedure if exists {perm_sp_name}(int)")


@pytest.mark.parametrize("execute_as", [None, "owner", "caller", "restricted caller"])
def test_execute_as_options(session, execute_as):
    """Make sure that a stored procedure can be run with any EXECUTE AS option."""

    def return1(_):
        return 1

    sproc_kwargs = {
        "return_type": IntegerType(),
    }
    if execute_as is not None:
        sproc_kwargs["execute_as"] = execute_as

    return1_sp = sproc(return1, **sproc_kwargs)
    assert return1_sp() == 1


@pytest.mark.parametrize("execute_as", [None, "owner", "caller", "restricted caller"])
def test_execute_as_options_while_registering_from_file(
    session, resources_path, tmpdir, execute_as
):
    """Make sure that a stored procedure can be run with any EXECUTE AS option, when registering from file."""
    sproc_kwargs = {"return_type": IntegerType(), "input_types": [IntegerType()]}
    if execute_as is not None:
        sproc_kwargs["execute_as"] = execute_as

    test_files = TestFiles(resources_path)
    mod5_sp = session.sproc.register_from_file(
        test_files.test_sp_py_file, "mod5", **sproc_kwargs
    )
    assert isinstance(mod5_sp.func, tuple)
    assert mod5_sp(3) == 3

    # test zip file
    from zipfile import ZipFile

    zip_path = f"{tmpdir.join(os.path.basename(test_files.test_sp_py_file))}.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.write(
            test_files.test_sp_py_file, os.path.basename(test_files.test_sp_py_file)
        )

    mod5_sp_zip = session.sproc.register_from_file(zip_path, "mod5", **sproc_kwargs)
    assert mod5_sp_zip(3) == 3

    # test a remote python file
    stage_file = f"@{tmp_stage_name}/{os.path.basename(test_files.test_sp_py_file)}"
    mod5_sp_stage = session.sproc.register_from_file(stage_file, "mod5", **sproc_kwargs)
    assert mod5_sp_stage(3) == 3


def test_call_sproc_with_session_as_first_argument(session):
    @sproc
    def return1(_: Session) -> int:
        return 1

    @sproc
    def plus1(_: Session, x: int) -> int:
        return x + 1

    assert return1(session) == 1
    assert plus1(session, 1) == 2

    with pytest.raises(ValueError) as ex_info:
        return1(session, session=session)
    assert "Two sessions specified in arguments" in str(ex_info)

    with pytest.raises(ValueError) as ex_info:
        plus1(session, 1, session=session)
    assert "Two sessions specified in arguments" in str(ex_info)


def test_strict_stored_procedure(session):
    @sproc(strict=True)
    def echo(_: Session, num: int) -> int:
        if num is None:
            raise ValueError("num should not be None")
        return num

    assert echo(None) is None


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="SNOW-1370056: Anonymous stored procedure is not supported yet",
)
def test_anonymous_stored_procedure(session):
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.create_dataframe([[x + y]]).collect()[0][0],
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        anonymous=True,
    )
    assert add_sp._anonymous_sp_sql is not None
    assert add_sp(1, 2) == 3


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="Query tag is a SQL only feature",
    run=False,
)
@pytest.mark.parametrize("anonymous", [True, False])
def test_stored_procedure_call_with_statement_params(session, anonymous):
    query_tag = f"QUERY_TAG_{Utils.random_alphanumeric_str(10)}"
    statement_params = {"QUERY_TAG": query_tag}
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.sql(f"SELECT {x} + {y}").collect()[0][0],
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
        anonymous=anonymous,
    )
    if anonymous:
        assert add_sp._anonymous_sp_sql is not None
    assert add_sp(1, 2, statement_params=statement_params) == 3
    Utils.assert_executed_with_query_tag(session, query_tag)


@pytest.mark.skipif(IS_NOT_ON_GITHUB, reason="need resources")
def test_sp_external_access_integration(session, db_parameters):
    def return_success(session_):
        import _snowflake
        import requests

        if (
            _snowflake.get_generic_secret_string("cred") == "replace-with-your-api-key"
            and requests.get("https://www.google.com").status_code == 200
        ):
            return "success"
        return "failure"

    try:
        return_success_sp = session.sproc.register(
            return_success,
            return_type=StringType(),
            packages=["requests", "snowflake-snowpark-python"],
            external_access_integrations=[
                db_parameters["external_access_integration1"]
            ],
            secrets={"cred": f"{db_parameters['external_access_key1']}"},
        )
        assert return_success_sp() == "success"
    except KeyError:
        pytest.skip("External Access Integration is not supported on the deployment.")


@pytest.mark.xfail(
    "config.getoption('local_testing_mode', default=False)",
    reason="This is a SQL test",
    run=False,
)
def test_force_inline_code(session):
    large_str = "snow" * 10000

    def f(session: Session) -> int:
        return len(large_str)

    with session.query_history() as query_history:
        _ = session.sproc.register(f, packages=["snowflake-snowpark-python"])
    assert all("AS $$" not in query.sql_text for query in query_history.queries)

    with session.query_history() as query_history:
        _ = session.sproc.register(
            f, packages=["snowflake-snowpark-python"], force_inline_code=True
        )
    assert any("AS $$" in query.sql_text for query in query_history.queries)


@pytest.mark.skip(
    reason="SNOW-1818207 conflict numpy dependency in snowpark python backend"
)
@pytest.mark.skipif(not is_pandas_available, reason="Requires pandas")
def test_stored_proc_register_with_module(session):
    # use pandas module here
    session.custom_package_usage_config["enabled"] = True
    packages = list(session.get_packages().values())
    assert "pd" not in packages
    packages = [pd] + packages

    def proc_function(session_: Session) -> str:
        return "test response"

    session.sproc.register(
        proc_function,
        source_code_display=False,
        packages=packages,
    )


@pytest.mark.skipif(
    IS_IN_STORED_PROC, reason="use schema is not allowed in stored proc (owner mode)"
)
@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="running sql query is not supported in local testing",
)
def test_register_sproc_after_switch_schema(session):
    add_sp = session.sproc.register(
        lambda session_, x, y: session_.create_dataframe([[x + y]]).collect()[0][0],
        return_type=IntegerType(),
        input_types=[IntegerType(), IntegerType()],
    )
    assert add_sp(1, 2) == 3

    current_schema = session.get_current_schema()
    current_database = session.get_current_database()

    databases = []
    try:
        for _ in range(2):
            new_database = f"db_{Utils.random_alphanumeric_str(10)}"
            databases.append(new_database)
            new_schema = f"{new_database}.test"

            session._run_query(f"create database if not exists {new_database}")
            session._run_query(f"create schema if not exists {new_schema}")
            session._run_query(f"use schema {new_schema}")

            add_sp = session.sproc.register(
                lambda session_, x, y: session_.create_dataframe([[x + y]]).collect()[
                    0
                ][0],
                return_type=IntegerType(),
                input_types=[IntegerType(), IntegerType()],
            )
            assert add_sp(1, 2) == 3
    finally:
        for db in databases:
            Utils.drop_database(session, db)
        session.use_database(current_database)
        session.use_schema(current_schema)


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="artifact repository not supported in local testing",
)
@pytest.mark.skipif(IS_NOT_ON_GITHUB, reason="need resources")
@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Stored proc env does not have permissions to look up warehouse details",
)
@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="artifact repository requires Python 3.9+"
)
def test_sproc_artifact_repository(session):
    def artifact_repo_test(_):
        import urllib3

        return str(urllib3.exceptions.HTTPError("test"))

    try:
        artifact_repo_sproc = sproc(
            artifact_repo_test,
            session=session,
            return_type=StringType(),
            artifact_repository="SNOWPARK_PYTHON_TEST_REPOSITORY",
            packages=["urllib3", "requests"],
        )
        assert artifact_repo_sproc(session=session) == "test"

        warehouse_info = (
            session.sql(
                f"show warehouses like '{unquote_if_quoted(session.get_current_warehouse())}'"
            )
            .select('"is_current"', '"resource_constraint"')
            .collect()
        )
        active, resource_constraint = warehouse_info[0]

        # Only test error case on ARM warehouse. X86 warehouse will have a resource constraint
        if len(warehouse_info) == 1 and active == "Y" and resource_constraint is None:
            try:
                artifact_repo_sproc = sproc(
                    artifact_repo_test,
                    session=session,
                    return_type=StringType(),
                    artifact_repository="SNOWPARK_PYTHON_TEST_REPOSITORY",
                    packages=["urllib3", "requests", "cloudpickle"],
                    resource_constraint={"architecture": "x86"},
                )
            except SnowparkSQLException as ex:
                assert "Cannot create on a Python function with 'X86' architecture annotation using an 'ARM' warehouse." in str(
                    ex
                ) or "Cannot create or execute a function with resource_constraint annotation on a standard warehouse." in str(
                    ex
                )
    except SnowparkSQLException as ex:
        if "No matching distribution found for snowflake-snowpark-python" in str(ex):
            pytest.mark.xfail(
                "Unreleased snowpark versions are unavailable in artifact repository."
            )


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="artifact repository not supported in local testing",
)
@pytest.mark.skipif(IS_NOT_ON_GITHUB, reason="need resources")
@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="artifact repository requires Python 3.9+"
)
def test_sproc_artifact_repository_from_file(session, tmpdir):
    source = dedent(
        """
    import snowflake
    import urllib3
    from snowflake.snowpark import Session
    def artifact_repo_test(session: snowflake.snowpark.Session) -> str:
        return str(urllib3.exceptions.HTTPError("test"))
    """
    )
    file_path = os.path.join(tmpdir, "artifact_repository_sproc.py")
    with open(file_path, "w") as f:
        f.write(source)

    artifact_repo_sproc = session.sproc.register_from_file(
        file_path,
        "artifact_repo_test",
        artifact_repository="SNOWPARK_PYTHON_TEST_REPOSITORY",
        packages=["urllib3", "requests", "snowflake-snowpark-python"],
    )
    assert artifact_repo_sproc(session=session) == "test"


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="packages unavailable in stored proc",
)
@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="Packaging processing is a NOOP in Local Testing",
    run=False,
)
@pytest.mark.parametrize(
    "version_override, expect_warning",
    [
        ("1.27.1", False),  # Bugfix version - no warning
        ("999.999.999", True),  # Major version change - expect warning
    ],
)
def test_snowpark_python_bugfix_version_warning(
    session, caplog, version_override, expect_warning
):
    def run_test_case(caplog, version_override, expect_warning):
        """Runs a test case with a given package version override and expected warning presence."""

        def plus1(session_, x):
            return x + 1

        with patch(
            "importlib.metadata.version",
            side_effect=lambda package_name: version_override
            if package_name == "snowflake-snowpark-python"
            else importlib.metadata.version(package_name),
        ), caplog.at_level(logging.WARNING):
            plus1_sp = sproc(
                plus1,
                return_type=IntegerType(),
                input_types=[IntegerType()],
                packages=["snowflake-snowpark-python==1.27.0"],
            )
            assert plus1_sp(lit(6)) == 7

        assert (
            "The version of package 'snowflake-snowpark-python' in the local"
            in caplog.text
        ) == expect_warning
        caplog.clear()

    run_test_case(caplog, version_override, expect_warning)


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="data source is not supported in local testing",
    run=False,
)
def test_datasource_put_file_and_copy_into_in_sproc(session):
    # The tests session.file.put API as well as the COPY INTO sql executed inside stored proc
    def upload_and_copy_into(session_):
        from snowflake.snowpark._internal.utils import (
            random_name_for_temp_object,
            TempObjectType,
        )

        table_name = random_name_for_temp_object(TempObjectType.TABLE)
        stage_name = random_name_for_temp_object(TempObjectType.STAGE)
        session_.sql(f"CREATE TEMPORARY TABLE {table_name} (col INT)").collect()
        session_.sql(f"CREATE TEMPORARY STAGE {stage_name}").collect()

        file_name = "data.csv"
        csv_filename = f"/tmp/{file_name}"
        with open(csv_filename, "w") as file:
            file.write("42\n")  # Sample data

        session_.file.put(
            csv_filename,
            f"@{stage_name}",
            overwrite=True,
        )
        session_.sql(
            f"COPY INTO {table_name} FROM @{stage_name}/{file_name} FILE_FORMAT = (TYPE = CSV)"
        ).collect()
        if session_.table(table_name).collect() == [(42,)]:
            return "success"
        else:
            return "failure"

    # sproc execution
    ingestion = sproc(upload_and_copy_into, return_type=StringType())
    assert ingestion() == "success"


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="data source is not supported in local testing",
    run=False,
)
def test_procedure_with_default_value(session):
    temp_sp_name = Utils.random_name_for_temp_object(TempObjectType.PROCEDURE)
    sql = f"""
create temporary procedure {temp_sp_name}(col1 INT, col2 STRING default 'snowflake')
returns table(col1 INT, col2 STRING)
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
packages = ('snowflake-snowpark-python', 'pandas')
HANDLER = 'my_handler'
AS $$
def my_handler(session, col1, col2):

    return session.create_dataframe([[col1,col2]],schema=['col1','col2'])
$$;
    """
    session.sql(sql).collect()
    df = session.call(temp_sp_name, 1, return_dataframe=True)
    Utils.check_answer(df, [Row(COL1=1, COL2="snowflake")])


@pytest.mark.skipif(
    "config.getoption('local_testing_mode', default=False)",
    reason="data source is not supported in local testing",
    run=False,
)
def test_datasource_put_file_stream_and_copy_into_in_sproc(session):
    def core_ingestion_logic(session_):
        from snowflake.snowpark._internal.utils import (
            random_name_for_temp_object,
            TempObjectType,
        )
        import multiprocessing as mp
        from io import BytesIO
        import pandas as pd

        queue = mp.Queue()

        def worker_process(parquet_queue):

            # Create a sample DataFrame
            data = {
                "id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Charlie", "David", "Eve"],
                "value": [100, 200, 300, 400, 500],
            }
            parquet_buffer = BytesIO()
            df = pd.DataFrame(data)
            df.to_parquet(parquet_buffer)
            parquet_buffer.seek(0)
            parquet_queue.put(parquet_buffer)

        process = mp.Process(target=worker_process, args=(queue,))
        process.start()

        # Wait for the process to complete
        process.join()
        if process.exitcode != 0 or process.is_alive():
            return "failure"

        # Get the parquet buffer from the queue
        parquet_buffer = queue.get()
        parquet_buffer.seek(0)

        # Create a temporary stage
        stage_name = random_name_for_temp_object(TempObjectType.STAGE)
        session_.sql(f"CREATE TEMPORARY STAGE {stage_name}").collect()

        # Create a temporary table
        table_name = random_name_for_temp_object(TempObjectType.TABLE)
        session_.sql(
            f"CREATE TEMPORARY TABLE {table_name} (id INT, name STRING, value INT)"
        ).collect()

        session_.file.put_stream(
            parquet_buffer, f"@{stage_name}/data.parquet", overwrite=True
        )

        # Copy the parquet buffer into the temporary table
        session_.sql(
            f"COPY INTO {table_name} FROM @{stage_name}/data.parquet FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME=CASE_INSENSITIVE"
        ).collect()

        # Check the results
        df = session_.table(table_name).collect()
        if df != [
            (1, "Alice", 100),
            (2, "Bob", 200),
            (3, "Charlie", 300),
            (4, "David", 400),
            (5, "Eve", 500),
        ]:
            return "failure"
        return "success"

    ingestion = sproc(core_ingestion_logic, return_type=StringType())
    assert ingestion() == "success"
