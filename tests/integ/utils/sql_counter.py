#!/usr/bin/env python
#
# Copyright (c) 2012-2025 Snowflake Computing Inc. All rights reserved.
#
import functools
import inspect
import os
import re
import sys
import threading
import traceback
from typing import Dict, List, Optional, Union

import pytest

from snowflake.snowpark.query_history import QueryListener, QueryRecord
from snowflake.snowpark.session import Session
from tests.utils import IS_IN_STORED_PROC

PythonScalar = Union[str, float, bool]

UPDATED_SUFFIX = "updated"
ORIGINAL_SUFFIX = "original"
STATUS_REPORT_FILE = "sql_counter_report.txt"

SQL_COUNT_CHECKER = "@sql_count_checker"
CALL = "CALL "
JOIN = " JOIN "
TEMP_PROCEDURE = "TEMP_PROCEDURE"
TEMP_FUNCTION = "TEMP_FUNCTION"
TEMP_TABLE_FUNCTION = "TEMP_TABLE_FUNCTION"
SELECT = "SELECT "
INSERT = "INSERT "
WITH = "WITH "
CREATE_TEMP_TABLE = "CREATE  TEMPORARY  TABLE"
UNION = " UNION "
WINDOW = " OVER "
WITH_SNOWPARK_TEMP_CTE = "WITH SNOWPARK_TEMP_CTE_"

NO_CHECK = "no_check"

QUERY_COUNT_PARAMETER = "query_count"
JOIN_COUNT_PARAMETER = "join_count"
SPROC_COUNT_PARAMETER = "sproc_count"
UDF_COUNT_PARAMETER = "udf_count"
UDTF_COUNT_PARAMETER = "udtf_count"
SELECT_COUNT_PARAMETER = "select_count"
UNION_COUNT_PARAMETER = "union_count"
WINDOW_COUNT_PARAMETER = "window_count"
DESCRIBE_COUNT_PARAMETER = "describe_count"
EXPECT_HIGH_COUNT = "expect_high_count"
HIGH_COUNT_REASON = "high_count_reason"

SQL_COUNT_PARAMETERS = [
    QUERY_COUNT_PARAMETER,
    JOIN_COUNT_PARAMETER,
    SPROC_COUNT_PARAMETER,
    UDF_COUNT_PARAMETER,
    UDTF_COUNT_PARAMETER,
    SELECT_COUNT_PARAMETER,
    UNION_COUNT_PARAMETER,
    DESCRIBE_COUNT_PARAMETER,
    WINDOW_COUNT_PARAMETER,
]
BOOL_PARAMETERS = [EXPECT_HIGH_COUNT]

SQL_COUNTER_CALLED = "sql_counter_called"

# The high count threshold is checked for each test, if the adjusted query count exceeds this threshold then
# the test will fail with an explanation on how to mitigate.
HIGH_QUERY_COUNT_THRESHOLD = 9


# The following queries aren't related to unit test queries and result in inconsistency in counts across environments.
# 1. create scoped stage generally occurs once in the session so not consistent when it executes
# 2. snowpark.zip already exists it won't upload again
# 3. snowpark will inline the python sproc in some cases so this results in inconsistent query counts, to mitigate this
# we don't count uploading the udf as a file since that doesn't always happen due to variation in generated byte code
# or compression size
# 4. select package_name, array_agg("VERSION") ... is to validate the package to be used for fallback stored procedure,
# it only runs at the first time of creating a fallback stored procedure
# 5. test_table_fixture does a drop table which is inconsistently included but ultimately not related to the tested code
# These cases should be excluded in our query counts.
# 6. Unused temp tables to be dropped to temp table cleaner may happen any time when garbage collection kicks in,
# so we should not count it
# 7. SHOW PARAMETERS LIKE 'QUOTED_IDENTIFIERS_IGNORE_CASE' IN SESSION ... is to validate at the beginning of the session
# that this parameter is unset, as currently required by Snowpark pandas.
FILTER_OUT_QUERIES = [
    ["create SCOPED TEMPORARY", "stage if not exists"],
    ["PUT", "file:///tmp/placeholder/snowpark.zip"],
    ["PUT", "file:///tmp/placeholder/udf_py_"],
    ['SELECT "PACKAGE_NAME"', 'array_agg("VERSION")'],
    ["drop table if exists", "TESTTABLENAME"],
    ["drop table if exists", "/* internal query to drop unused temp table */"],
    ["SHOW PARAMETERS LIKE", "QUOTED_IDENTIFIERS_IGNORE_CASE"],
]

# define global at module-level
sql_count_records = {}

sql_counter_state = threading.local()
sql_counter_lock = threading.RLock()


class SqlCounter(QueryListener):
    """
    SqlCounter is an object that counts metrics related to snowpark queries.  This includes things like query counts
    and join counts.  It can be extended to cover other counts as well.

    See SQL_COUNT_PARAMETERS for list of currently supported sql counts.

    SqlCounter can be invoked through the sql_count_checker decorator or instantiated directly.  When providing count
    parameters, if any count parameter is not provided it assumed to have expected value of 0, so these parameters
    do not need to be explicitly provided.  Here is an example:

        with SqlCounter(query_count=5, udf_count=1):
            ...

        This will check the query_count is 5 and udf_count is 1, and *all* other counts (like udtf_count, etc)
        are expected to equal 0.

    If we do not expect the test to invoke any queries, we still recommend to at least explicitly check the
    query_count is 0 through the sql_count_checker decorator.  If we do not check at all then if the underlying code
    changes, and later introduce queries we would not catch this.  In the rare case where we need to ensure that no
    query validation happens (as opposed to validating no query happens) we can use the no_check=True argument.

    When strict=False the expected query counts are used as upper bounds.
    """

    _record_mode = False

    def __init__(
        self,
        no_check=False,
        log_stack_trace=True,
        high_count_expected=False,
        high_count_reason=None,
        strict=True,
        **kwargs,
    ) -> "SqlCounter":
        from tests.conftest import SKIP_SQL_COUNT_CHECK

        self._queries: list[QueryRecord] = []

        # Track the thread id to ensure we only count queries from the current thread.
        self._current_thread_id = threading.get_ident()

        # Bypassing sql counter since
        #   1. it is an unnecessary metric for tests running in stored procedures
        #   2. pytest-assume package is not available in conda
        self._no_check = no_check or IS_IN_STORED_PROC or SKIP_SQL_COUNT_CHECK

        # Save any expected sql counts initialized at start up.
        self._expected_sql_counts = {}
        for key, value in kwargs.items():
            if key not in SQL_COUNT_PARAMETERS:
                raise ValueError(f"Unrecognized parameter to SqlCounter '{key}'")
            self._expected_sql_counts[key] = value

        # Setup lookup functions to calculate different counts later.
        count_params = list(filter(lambda x: x.startswith("actual_"), dir(self)))
        self._actual_sql_count_helpers = {
            ct[len("actual_") :]: self.__getattribute__(ct) for ct in count_params
        }

        # Record mode is used when auto-annotating step runs.
        self._record_mode = False

        # Strict mode checks that query counts are exactly as expected. Non-strict
        # checks that queries are at most the expected counts.
        self._strict = strict

        self._log_stack_trace = log_stack_trace

        self._expect_high_count = high_count_expected
        self._high_count_reason = high_count_reason

        if self._no_check:
            self.session = None
        else:
            self.session = Session.SessionBuilder().getOrCreate()

        if self.session:
            # Add SqlCounter as a snowpark query listener.
            self.session._conn.add_query_listener(self)

    # The query history listener will include describe queries if this is true.
    @property
    def include_describe(self) -> bool:
        return True

    @property
    def include_thread_id(self) -> bool:
        return True

    @staticmethod
    def set_record_mode(record_mode):
        """Record mode means the SqlCounter does not assert any results, but rather collects them so they can
        be inspected separately, such as with the auto-annotation step."""
        SqlCounter._record_mode = record_mode

    def clear(self):
        """Reset the SqlCounter to start counting from 0."""
        self._queries = []

    def __enter__(self):
        """Context manager enter by resetting counts."""
        self.clear()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit checks for expected sql counts."""
        # If we're exiting this context because of an exception, i.e. exc_type
        # is not None, there's no need to check the SQL counts (see
        # SNOW-1042244).
        if exc_type is None:
            self.expects(**self._expected_sql_counts)
        if self.session is not None:
            self.session._conn.remove_query_listener(self)
        self._mark_as_dead()

    def _notify(self, query_record: QueryRecord, **kwargs: dict):
        if not is_suppress_sql_counter_listener():
            if query_record.thread_id == self._current_thread_id:
                self._queries.append(query_record)

    def expects(self, **kwargs):
        """
        Validate expectation of sql counts.  We avoid using asserts because we do not want to interrupt the
        test run if this fails, since it would mask functional failures.  By using pytest assume the validation
        happens at the end of the test.
        """
        mark_sql_counter_called()
        self._check_if_dead()

        if self._no_check or SqlCounter._record_mode:
            return

        if len(kwargs) == 0:
            raise AssertionError("SqlCounter not configured for test.")

        failed = False
        # Get the actual counts and check they match assumptions.
        actual_counts = self.get_actual_counts()
        stack_trace = (
            "\nOriginal stack trace:\n"
            + "".join(
                filter(lambda s: "site-package" not in s, traceback.format_stack())
            )
            if self._log_stack_trace
            else ""
        )

        for key in kwargs.keys():
            if key in BOOL_PARAMETERS:
                continue
            actual_count = actual_counts[key] if key in actual_counts else 0
            expected_count = kwargs[key]
            if expected_count is None:
                expected_count = 0
            valid_count = (
                expected_count == actual_count
                if self._strict
                else actual_count <= expected_count
            )
            failed = failed or not valid_count
            pytest.assume(
                valid_count,
                f"Sql count check '{key}' failed.  expected_{key}={expected_count}, actual_{key}={actual_count}{stack_trace}",
            )

        # If there are no failures, then check if we fail due to high query count.
        if not failed:
            query_count = (
                actual_counts[QUERY_COUNT_PARAMETER]
                if QUERY_COUNT_PARAMETER in kwargs.keys()
                else 0
            )
            high_query_count_check = query_count <= HIGH_QUERY_COUNT_THRESHOLD
            suppress_high_query_count = (
                self._expect_high_count and self._high_count_reason is not None
            )

            pytest.assume(
                high_query_count_check or suppress_high_query_count,
                f"""
    Sql count check '{QUERY_COUNT_PARAMETER}' failed on high query count, query_count={query_count}>{HIGH_QUERY_COUNT_THRESHOLD}
    The test is generating too many queries, please investigate the high query count for potential performance problems
    and/or consider refactoring the sql count checks to be more granular.  To suppress this failure, please create a
    jira (if there is a follow up action required) and add the arguments 'high_count_expected=True' and
    'high_count_reason="SNOW-JIRA# <Explanation>' with explanation for suppression to the sql check.
                """,
            )
            failed = True

        # If there are any failures, print out all the captured queries so clear which are being counted.
        if failed:
            title = f"\n{'='*20} SqlCounter Captured Queries {'='*20}\n"
            print(title, file=sys.stderr)
            for query in self._get_actual_queries():
                print(query, file=sys.stderr)
            print("=" * len(title), file=sys.stderr)

        self.clear()

    def _normalize_sql(self, sql: str) -> str:
        """Normalize SQL query by
        converting to uppercase and removing
        tabs and new lines.
        """
        sql = sql.upper()
        sql = re.sub(r"\s+", " ", sql)
        sql = re.sub(r"\(\s+", "(", sql)
        sql = re.sub(r"\s+\)", ")", sql)
        return sql

    def _get_actual_queries(self):
        """Get actual queries after filtering out system queries and normalizing."""
        return list(
            filter(
                lambda q: not any(
                    [
                        all(
                            [
                                self._normalize_sql(p) in self._normalize_sql(q)
                                for p in fw
                            ]
                        )
                        for fw in FILTER_OUT_QUERIES
                    ]
                ),
                list(
                    map(
                        lambda q: self._normalize_sql(q.sql_text),
                        [q for q in self._queries if not q.is_describe],
                    )
                ),
            )
        )

    def _count_by_query_substr(self, starts_with=None, contains=None):
        if starts_with is None:
            starts_with = []
        if contains is None:
            contains = []

        # Normalize the search patterns
        starts_with = [self._normalize_sql(sw) for sw in starts_with]
        contains = [self._normalize_sql(c) for c in contains]

        return sum(
            bool(x)
            for x in map(
                lambda q: (
                    not starts_with or any([q.startswith(sw) for sw in starts_with])
                )
                and all([c in q for c in contains]),
                self._get_actual_queries(),
            )
        )

    def _count_instances_by_query_substr(self, starts_with=None, contains=None):
        starts_with = starts_with or []
        contains = contains or []

        starts_with = [self._normalize_sql(sw) for sw in starts_with]
        contains = [self._normalize_sql(c) for c in contains]

        return sum(
            map(
                lambda q: sum(
                    [q.count(c) for c in contains]
                    if (
                        not starts_with or any([q.startswith(sw) for sw in starts_with])
                    )
                    else [0]
                ),
                self._get_actual_queries(),
            )
        )

    def actual_query_count(self):
        """Return number of sql queries"""
        return len(self._get_actual_queries())

    def actual_join_count(self):
        """Return number of joins across all sql queries"""
        return self._count_instances_by_query_substr(contains=[JOIN])

    def actual_sproc_count(self):
        return self._count_by_query_substr(contains=[CALL])

    def actual_udtf_count(self):
        return self._count_by_query_substr(
            [SELECT, INSERT, CREATE_TEMP_TABLE, WITH_SNOWPARK_TEMP_CTE],
            [TEMP_TABLE_FUNCTION],
        )

    def actual_udf_count(self):
        return self._count_by_query_substr([SELECT, INSERT], [TEMP_FUNCTION])

    def actual_select_count(self):
        return self._count_by_query_substr([SELECT])

    def actual_union_count(self):
        return self._count_instances_by_query_substr(contains=[UNION])

    def actual_window_count(self):
        return self._count_instances_by_query_substr(contains=[WINDOW])

    def actual_describe_count(self):
        return len([q for q in self._queries if q.is_describe])

    def get_actual_counts(self):
        """Retrieve all actual counts so far."""
        actual_counts = {}
        for key, get_count in self._actual_sql_count_helpers.items():
            actual_counts[key] = get_count()
        return actual_counts

    def _check_if_dead(self):
        """If SqlCounter is declared dead, it should not be used again."""
        if not self._no_check:
            assert (
                self.session is not None
            ), "SqlCounter is dead and can no longer be used."

    def _mark_as_dead(self):
        """Mark the SqlCounter as dead so it can no longer be used."""
        self.session = None


def sql_count_checker(
    no_check=None,
    high_count_expected=False,
    high_count_reason=None,
    query_count=None,
    join_count=None,
    sproc_count=None,
    udf_count=None,
    udtf_count=None,
    union_count=None,
    *args,
    **kwargs,
):
    """
    SqlCounter decorator that automatically validates the sql counts when test finishes.

    The sql count checks are applied for all parameters in the format of *_count, for example,
    join_count = 2 means we expect to see two joins in the sql queries.

    Note that the *_count can be configured in two ways: clear declaration in the sql_count_check in
    the signature, or passed through **kwargs. The check for count clearly declared in the signature
    will be enforced, and 0 occurrence is expected if the value is None. Other count checks can be
    optionally passed through the **kwargs, where the occurrence check will only be applied if specified.
    """
    all_args = inspect.getargvalues(inspect.currentframe())
    count_kwargs = {
        key: value
        for key, value in list(
            filter(lambda k: k[0].endswith("_count"), all_args.locals.items())
        )
    }
    # also look into kwargs for count configuration. Right now, describe_count and window_count are the
    # counts can be passed optionally
    for (key, value) in kwargs.items():
        if key.endswith("_count"):
            count_kwargs.update({key: value})

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            sql_counter = SqlCounter(
                no_check=no_check,
                log_stack_trace=False,
                high_count_expected=high_count_expected,
                high_count_reason=high_count_reason,
            )

            result = func(*args, **kwargs)
            try:
                sql_counter.expects(**count_kwargs)
            finally:
                try:
                    sql_counter.close()
                except Exception:
                    pass
            return result

        return wrapper

    return decorator


def get_readable_sql_count_values(tr):
    count_values = ", ".join(
        [
            f"{key}={tr[key]}"
            for key in SQL_COUNT_PARAMETERS
            if key in tr and tr[key] and tr[key] != 0
        ]
    )
    if len(count_values) == 0:
        return f"{SQL_COUNT_PARAMETERS[0]}=0"
    return count_values


def update_test_code_with_sql_counts(
    sql_count_records: Dict[str, Dict[str, List[Dict[str, Optional[PythonScalar]]]]]
):
    """This helper takes sql count records and rewrites the source test files to validate sql counts where possible.

    This also creates a status report file that is useful to summarize the actions taken or that need to be taken
    for instrumenting the sql_counter checks.  The status report file will include results for all tests that were
    part of the run, the file is in the tests root file path.  Each section will be named by the test_file and include
    a line per test_file, test_parameter combination along with the status.
    """
    last_status_file = None

    # Iterate through each sql count record, this is nested dictionary structured as:
    #     sql_count_records[test_file][test_name] -> dict[Str, PythonScalar]
    # The valid keys are:
    #     "test_name" for alternative reference
    #     "test_parms" if the test is parameterized
    #     "query_count" actual query count from run
    #     "join_count" actual join count from run
    #    ... any other counts added in the future ...
    for test_file in sql_count_records:
        test_file_record = sql_count_records[test_file]

        input_file = test_file
        out_file = f"{test_file}.{UPDATED_SUFFIX}"
        orig_file = f"{test_file}.{ORIGINAL_SUFFIX}"

        test_path = test_file.dirname.split("/")
        tests_index = test_path.index("tests")
        status_file = f"{'/'.join(test_path[:tests_index])}/{'.'.join(test_path[tests_index:])}.{STATUS_REPORT_FILE}"

        # Remove any left over files from earlier full or partial runs.
        if status_file != last_status_file and os.path.exists(status_file):
            os.remove(status_file)

        if os.path.exists(out_file):
            os.remove(out_file)

        if os.path.exists(orig_file):
            os.remove(orig_file)

        # Accumulate status lines for added annotation and not added.
        added_status = []
        not_added_status = []

        out_lines = []
        with open(input_file) as src_file:
            for line in src_file.readlines():
                # Find the next test method in source file.
                if line.lstrip().startswith("def test"):
                    line_indent = " " * line.index("def")
                    test_name = re.split(r"[ |\(]", line.lstrip())[1]

                    if test_name in test_file_record:
                        test_records = test_file_record[test_name]
                        tr0 = test_records[0]
                        count_values = get_readable_sql_count_values(tr0)

                        # Check if we can add a decorator here, all parameterized tests must have same counts.
                        if len(test_records) == 1 or all(
                            [
                                all(
                                    [
                                        tr[key] == tr0[key]
                                        for key in SQL_COUNT_PARAMETERS
                                    ]
                                )
                                for tr in test_records[1:]
                            ]
                        ):
                            out_line = f"{SQL_COUNT_CHECKER}({count_values})"
                            skip_line = False
                            if len(out_lines) > 0:
                                if out_lines[-1].lstrip().startswith(out_line):
                                    not_added_status.append(
                                        (test_name, f"Already has {out_line}")
                                    )
                                    skip_line = True
                                else:
                                    no_check_checker = (
                                        f"{SQL_COUNT_CHECKER}({NO_CHECK}=True)"
                                    )
                                    if (
                                        out_lines[-1]
                                        .lstrip()
                                        .startswith(no_check_checker)
                                    ):
                                        not_added_status.append(
                                            (
                                                test_name,
                                                f"Skipping since no_check {no_check_checker}",
                                            )
                                        )
                                        skip_line = True

                            if not skip_line:
                                if len(out_lines) > 0 and out_lines[
                                    -1
                                ].lstrip().startswith(SQL_COUNT_CHECKER):
                                    added_status.append(
                                        (test_name, f"Updated {out_line}")
                                    )
                                    out_lines.pop()
                                else:
                                    added_status.append(
                                        (test_name, f"Added {out_line}")
                                    )

                                out_lines.append(f"{line_indent}{out_line}\n")
                        else:
                            if len(out_lines) > 0 and out_lines[-1].lstrip().startswith(
                                SQL_COUNT_CHECKER
                            ):
                                added_status.append(
                                    (test_name, f"Removing {out_lines[-1]}")
                                )
                                out_lines.pop()

                            for tr in test_records:
                                count_values = get_readable_sql_count_values(tr)
                                test_parms = tr["test_parms"]
                                not_added_status.append(
                                    (
                                        f"{test_name}[{test_parms}]",
                                        f"Please add inline sql_count_checker code: {count_values}",
                                    )
                                )
                    else:
                        not_added_status.append(
                            (test_name, "Unable to find test result, probably skipped")
                        )

                # We insert the import for sql_count_checker.  We don't guarantee the correct import ordering, so
                # will need to run the lint to re-order and eliminate any duplicates that may occur.
                if len(out_lines) > 0 and out_lines[-1].startswith("import pytest"):
                    out_lines.append(
                        "from tests.sql_counter import sql_count_checker\n"
                    )

                out_lines.append(line)

        last_status_file = status_file

        with open(out_file, "x") as new_src_file:
            for line in out_lines:
                new_src_file.write(line)

        indent_len = max(len(st[0]) for st in added_status + not_added_status) + 1

        banner = "=" * indent_len
        with open(status_file, "a") as status_file:
            line = f"\n{banner}\nTest file: {input_file}\n{banner}"
            print(line)
            status_file.write(f"{line}\n")
            for st in added_status + not_added_status:
                line = f"{st[0]:<{indent_len}}{st[1]}"
                print(line)
                status_file.write(f"{line}\n")

        os.rename(test_file, orig_file)
        os.rename(out_file, input_file)


def generate_sql_count_report(request, counter):
    """
    Helper function called to rewrite source files with sql_count_checker decorators based on record mode test run.
    """
    global sql_count_records
    src_file = request.fspath
    test_name_with_parms = request.node.name

    if request.session.items[0].name == test_name_with_parms:
        sql_count_records = {}

    if src_file not in sql_count_records:
        sql_count_records[src_file] = {}

    actual_counts = counter.get_actual_counts()

    file_count_records = sql_count_records[src_file]

    test_name, test_parms = re.split(r"[\[|\]]", f"{test_name_with_parms}[]")[:2]

    if test_name not in file_count_records:
        file_count_records[test_name] = []

    test_count_records = file_count_records[test_name]

    test_count_record = {}
    test_count_record["test_name"] = test_name
    test_count_record["test_parms"] = test_parms
    for key, value in actual_counts.items():
        test_count_record[key] = value
    test_count_records.append(test_count_record)

    if request.session.items[-1].name == test_name_with_parms:
        update_test_code_with_sql_counts(sql_count_records)


def mark_sql_counter_called():
    with sql_counter_lock:
        threading.main_thread().__dict__[SQL_COUNTER_CALLED] = True


def clear_sql_counter_called():
    with sql_counter_lock:
        threading.main_thread().__dict__[SQL_COUNTER_CALLED] = False


def is_sql_counter_called():
    with sql_counter_lock:
        return threading.main_thread().__dict__.get(SQL_COUNTER_CALLED, False)
    return False


def enable_sql_counting():
    sql_counter_state.suppress_sql_counter_listener = False


def suppress_sql_counting():
    sql_counter_state.suppress_sql_counter_listener = True


def is_suppress_sql_counter_listener():
    return (
        hasattr(sql_counter_state, "suppress_sql_counter_listener")
        and sql_counter_state.suppress_sql_counter_listener
    )
