#
# Copyright (c) 2012-2025 Snowflake Computing Inc. All rights reserved.
#

import importlib
import inspect
import json
import math
import re
import statistics
import typing
import uuid
from collections import defaultdict
from collections.abc import Iterable
from enum import Enum
from functools import cached_property, partial, reduce
from typing import TYPE_CHECKING, Any, Dict, List, NoReturn, Optional, Union, Tuple
from unittest.mock import MagicMock

from snowflake.snowpark._internal.analyzer.select_statement import SelectTableFunction
from snowflake.snowpark._internal.analyzer.table_function import TableFunctionJoin
from snowflake.snowpark._internal.analyzer.table_merge_expression import (
    DeleteMergeExpression,
    InsertMergeExpression,
    TableDelete,
    TableMerge,
    TableUpdate,
    UpdateMergeExpression,
)
from snowflake.snowpark._internal.analyzer.window_expression import (
    CurrentRow,
    FirstValue,
    Lag,
    LastValue,
    Lead,
    RangeFrame,
    RowFrame,
    SpecifiedWindowFrame,
    UnboundedFollowing,
    UnboundedPreceding,
    WindowExpression,
)
from snowflake.snowpark.mock._udf_utils import coerce_variant_input, remove_null_wrapper
from snowflake.snowpark.mock._util import ImportContext, get_fully_qualified_name
from snowflake.snowpark.mock._window_utils import (
    EntireWindowIndexer,
    RowFrameIndexer,
    is_rank_related_window_function,
)
from snowflake.snowpark.mock.exceptions import SnowparkLocalTestingException

if TYPE_CHECKING:
    from snowflake.snowpark.mock._analyzer import MockAnalyzer

from snowflake.snowpark._internal.analyzer.analyzer_utils import (
    EXCEPT,
    INTERSECT,
    UNION,
    UNION_ALL,
    quote_name,
)
from snowflake.snowpark._internal.analyzer.binary_expression import (
    Add,
    And,
    BinaryExpression,
    BitwiseAnd,
    BitwiseOr,
    BitwiseXor,
    Divide,
    EqualNullSafe,
    EqualTo,
    GreaterThan,
    GreaterThanOrEqual,
    LessThan,
    LessThanOrEqual,
    Multiply,
    NotEqualTo,
    Or,
    Pow,
    Remainder,
    Subtract,
)
from snowflake.snowpark._internal.analyzer.binary_plan_node import Join
from snowflake.snowpark._internal.analyzer.expression import (
    Attribute,
    CaseWhen,
    ColumnSum,
    Expression,
    FunctionExpression,
    InExpression,
    Interval,
    Like,
    ListAgg,
    Literal,
    MultipleExpression,
    RegExp,
    ScalarSubquery,
    SnowflakeUDF,
    Star,
    SubfieldInt,
    SubfieldString,
    UnresolvedAttribute,
    WithinGroup,
)
from snowflake.snowpark._internal.analyzer.query_plan_analysis_utils import PlanState
from snowflake.snowpark._internal.analyzer.snowflake_plan import (
    PlanQueryType,
    Query,
    SnowflakePlan,
    QueryLineInterval,
)
from snowflake.snowpark._internal.analyzer.snowflake_plan_node import (
    LogicalPlan,
    Range,
    SaveMode,
    SnowflakeCreateTable,
    SnowflakeTable,
    SnowflakeValues,
)
from snowflake.snowpark._internal.analyzer.sort_expression import (
    Ascending,
    NullsFirst,
    SortOrder,
)
from snowflake.snowpark._internal.analyzer.unary_expression import (
    Alias,
    Cast,
    IsNaN,
    IsNotNull,
    IsNull,
    Not,
    UnaryMinus,
    UnresolvedAlias,
)
from snowflake.snowpark._internal.analyzer.unary_plan_node import (
    Aggregate,
    CreateViewCommand,
    Pivot,
    Sample,
    Project,
    SampleBy,
)
from snowflake.snowpark._internal.type_utils import infer_type
from snowflake.snowpark._internal.utils import (
    generate_random_alphanumeric,
    parse_table_name,
)
from snowflake.snowpark.column import Column
from snowflake.snowpark.mock._functions import MockedFunctionRegistry, cast_column_to
from snowflake.snowpark.mock._options import pandas as pd
from snowflake.snowpark.mock._select_statement import (
    MockSelectable,
    MockSelectableEntity,
    MockSelectExecutionPlan,
    MockSelectStatement,
    MockSetStatement,
)
from snowflake.snowpark.mock._snowflake_data_type import (
    ColumnEmulator,
    ColumnType,
    TableEmulator,
    get_coerce_result_type,
)
from snowflake.snowpark.mock._util import (
    convert_wildcard_to_regex,
    custom_comparator,
    fix_drift_between_column_sf_type_and_dtype,
)
from snowflake.snowpark.row import Row
from snowflake.snowpark.types import (
    BooleanType,
    ByteType,
    DateType,
    DecimalType,
    DoubleType,
    FloatType,
    IntegerType,
    LongType,
    NullType,
    ShortType,
    StringType,
    TimestampType,
    VariantType,
    _NumericType,
)


class MockExecutionPlan(LogicalPlan):
    def __init__(
        self,
        source_plan: LogicalPlan,
        session,
        *,
        child: Optional["MockExecutionPlan"] = None,
        expr_to_alias: Optional[Dict[uuid.UUID, str]] = None,
        df_aliased_col_name_to_real_col_name: Optional[Dict[str, str]] = None,
    ) -> NoReturn:
        super().__init__()
        self.source_plan = source_plan
        self.session = session
        self.schema_query = None
        self.uuid = "MOCK_UUID"
        mock_query = MagicMock()
        mock_query.sql = "SELECT MOCK_TEST_FAKE_QUERY()"
        mock_query.query_line_intervals = [QueryLineInterval(0, 0, self.uuid)]
        self.queries = [mock_query]
        self.child = child
        self.expr_to_alias = expr_to_alias if expr_to_alias is not None else {}
        self.df_aliased_col_name_to_real_col_name = (
            df_aliased_col_name_to_real_col_name or defaultdict(dict)
        )
        self.api_calls = []
        self._attributes = None
        self.df_ast_ids = None

    @property
    def attributes(self) -> List[Attribute]:
        return describe(self)

    @cached_property
    def output(self) -> List[Attribute]:
        return [Attribute(a.name, a.datatype, a.nullable) for a in self.attributes]

    @cached_property
    def plan_state(self) -> Dict[PlanState, Any]:
        # dummy return
        return {
            PlanState.PLAN_HEIGHT: -1,
            PlanState.NUM_SELECTS_WITH_COMPLEXITY_MERGED: -1,
            PlanState.NUM_CTE_NODES: -1,
            PlanState.DUPLICATED_NODE_COMPLEXITY_DISTRIBUTION: [],
        }

    @property
    def post_actions(self):
        return []

    @property
    def execution_queries(self) -> Dict[PlanQueryType, List[Query]]:
        return {
            PlanQueryType.QUERIES: self.queries,
            PlanQueryType.POST_ACTIONS: self.post_actions,
        }

    def add_aliases(self, to_add: Dict) -> None:
        self.expr_to_alias.update(to_add)

    def add_df_ast_id(self, ast_id: int) -> None:
        if self.df_ast_ids is None:
            self.df_ast_ids = [ast_id]
        elif self.df_ast_ids[-1] != ast_id:
            self.df_ast_ids.append(ast_id)


class MockFileOperation(MockExecutionPlan):
    class Operator(str, Enum):
        PUT = "put"
        GET = "get"
        READ_FILE = "read_file"
        # others are not supported yet

    def __init__(
        self,
        session,
        operator: Union[str, Operator],
        *,
        options: Dict[str, str],
        local_file_name: Optional[str] = None,
        stage_location: Optional[str] = None,
        child: Optional["MockExecutionPlan"] = None,
        source_plan: Optional[LogicalPlan] = None,
        format: Optional[str] = None,
        schema: Optional[List[Attribute]] = None,
    ) -> None:
        super().__init__(session=session, child=child, source_plan=source_plan)
        self.operator = operator
        self.local_file_name = local_file_name
        self.stage_location = stage_location
        self.api_calls = self.api_calls or []
        self.format = format
        self.schema = schema
        self.options = options


def coerce_order_by_arguments(order_by: List[Expression]):
    return [
        order if isinstance(order, SortOrder) else SortOrder(order, Ascending())
        for order in order_by
    ]


def handle_order_by_clause(
    order_by: List[SortOrder],
    result_df: TableEmulator,
    analyzer: "MockAnalyzer",
    expr_to_alias: Optional[Dict[str, str]],
    keep_added_columns: bool = False,
) -> TableEmulator:
    """Given an input dataframe `result_df` and a list of SortOrder expressions `order_by`, return the sorted dataframe."""
    sort_columns_array = []
    sort_orders_array = []
    null_first_last_array = []
    added_columns = []
    for exp in order_by:
        exp_name = analyzer.analyze(exp.child, expr_to_alias)
        if exp_name not in result_df.columns:
            result_df[exp_name] = calculate_expression(
                exp.child, result_df, analyzer, expr_to_alias
            )
            added_columns.append(exp_name)
        sort_columns_array.append(exp_name)
        sort_orders_array.append(isinstance(exp.direction, Ascending))
        null_first_last_array.append(
            isinstance(exp.null_ordering, NullsFirst) or exp.null_ordering == NullsFirst
        )
    for column, ascending, null_first in reversed(
        list(zip(sort_columns_array, sort_orders_array, null_first_last_array))
    ):
        comparator = partial(custom_comparator, ascending, null_first)
        result_df = result_df.sort_values(by=column, key=comparator)

    result_df.sorted_by = sort_columns_array
    if not keep_added_columns:
        result_df = result_df.drop(columns=added_columns)

    return result_df


def validate_interval(interval: Optional[Union[Interval, int]]) -> bool:
    """
    Validates that the input value is valid for use as an interval.
        None -> Unbounded interval either before or after the current row.
        0 -> Use the current row and the bound.
        Interval -> Use the Interval when calculating the bound.
    """
    if interval is None or interval == 0 or isinstance(interval, Interval):
        return True
    return False


def negate_interval(interval: Interval) -> Interval:
    """
    Negates each value in an interval. Returns a new Interval object.
    """
    return Interval(
        **{key.lower(): -value for key, value in interval.values_dict.items()}
    )


def compare_intervals(
    lower: Optional[Union[Interval, int]], upper: Optional[Union[Interval, int]]
) -> bool:
    """
    Returns true if lower >= upper.
    """
    if lower is None or upper is None:
        return True

    lower = lower or Interval()
    upper = upper or Interval()

    for part in [
        "YEAR",
        "QUARTER",
        "MONTH",
        "WEEK",
        "DAY",
        "HOUR",
        "MINUTE",
        "SECOND",
        "MILLISECOND",
        "MICROSECOND",
        "NANOSECOND",
    ]:
        lower_part = lower.values_dict.get(part)
        upper_part = upper.values_dict.get(part)

        if lower_part or upper_part:
            lower_part = lower_part or 0
            upper_part = upper_part or 0
            if (lower_part + upper_part) != 0:
                return lower_part >= upper_part
    return True


def apply_interval(
    column: ColumnEmulator,
    interval: Interval,
    registry: Optional[MockedFunctionRegistry] = None,
) -> ColumnEmulator:
    """
    Applies an interval object to a datetime column by repeatedly calling dateadd on the interval parts.
    """
    # The expression `select to_date ('2019-02-28') + INTERVAL '1 day, 1 year';`
    # is rewritten to `select dateadd(DAY, 1, dateadd(YEAR, 1, to_date ('2019-02-28')))`.
    registry = registry or MockedFunctionRegistry.get_or_create()
    new_column = column
    for k, v in interval.values_dict.items():
        new_column = registry.get_function("dateadd")(k, v, new_column)
    return new_column


def handle_range_frame_indexing(
    order_spec: List[SortOrder],
    res_index: "pd.Index",
    res: "pd.api.typing.DataFrameGroupBy",
    analyzer: "MockAnalyzer",
    expr_to_alias: Dict[str, str],
    unbounded_preceding: bool,
    unbounded_following: bool,
    range_bounds: Optional[Tuple[int]] = None,
) -> "pd.api.typing.RollingGroupby":
    """Return a list of range between window frames based on the dataframe paritions `res` and the ORDER BY clause `order_spec`."""

    def search_boundary_idx(idx, delta, _win):
        while 0 <= idx + delta < len(_win):
            cur_expr = list(
                calculate_expression(exp.child, _win.iloc[idx], analyzer, expr_to_alias)
                for exp in order_spec
            )
            next_expr = list(
                calculate_expression(
                    exp.child, _win.iloc[idx + delta], analyzer, expr_to_alias
                )
                for exp in order_spec
            )
            if not cur_expr == next_expr:
                break
            idx += delta
        return idx

    if order_spec:
        ordered_windows = [
            handle_order_by_clause(order_spec, win, analyzer, expr_to_alias)
            for win in res.rolling(EntireWindowIndexer())
        ]
        types = ordered_windows[0].sf_types
        windows = []
        if range_bounds:
            group_col = analyzer.analyze(order_spec[0].child, expr_to_alias)
            lower, upper = range_bounds
            if isinstance(types[group_col].datatype, (DateType, TimestampType)):
                # Intervals are handled by adding the interval to the window and then
                # comparing to the current rows value. Normally the interval would be applied to
                # the value rather than the window data, but it's more efficient to do it this way.
                # Therefore intervals are expected to be passed negated.

                if (
                    isinstance(types[group_col].datatype, DateType)
                    and (lower is None or isinstance(lower, int))
                    and (upper is None or isinstance(upper, int))
                ):
                    if lower:
                        lower = Interval(day=-lower)
                    if upper:
                        upper = Interval(day=-upper)
                if not (validate_interval(lower) and validate_interval(upper)):
                    raise SnowparkLocalTestingException(
                        "Mixing numeric and interval frames is not supported"
                    )
                if not compare_intervals(lower, upper):
                    raise SnowparkLocalTestingException("Invalid window frame")
                for current_row, win in zip(res_index, ordered_windows):
                    cond = pd.Series([True] * len(win), index=win.index)
                    if lower is not None:
                        bound = (
                            win[group_col]
                            if lower == 0
                            else apply_interval(win[group_col], lower)
                        )
                        cond &= pd.Series(bound) >= win.loc[current_row][group_col]
                    if upper is not None:
                        bound = (
                            win[group_col]
                            if upper == 0
                            else apply_interval(win[group_col], upper)
                        )
                        cond &= pd.Series(bound) <= win.loc[current_row][group_col]
                    windows.append(
                        TableEmulator(pd.DataFrame(win).loc[cond], sf_types=types)
                    )
            elif isinstance(types[group_col].datatype, _NumericType):
                if isinstance(lower, Interval) or isinstance(upper, Interval):
                    raise SnowparkLocalTestingException(
                        "numeric ORDER BY clause only allows numeric window frame boundaries"
                    )
                # TableEmulator breaks loc comparisons
                cast_windows = [pd.DataFrame(win) for win in ordered_windows]
                for current_row, win in zip(res_index, cast_windows):
                    cond = pd.Series([True] * len(win), index=win.index)
                    if lower is not None:
                        cond &= (
                            win[group_col] >= win.loc[current_row][group_col] + lower
                        )
                    if upper is not None:
                        cond &= (
                            win[group_col] <= win.loc[current_row][group_col] + upper
                        )

                    # Cast back to TableEmulator so downstream can infer types correctly
                    windows.append(TableEmulator(win.loc[cond], sf_types=types))
            else:
                raise SnowparkLocalTestingException(
                    "range_between only operates on numeric or datetime group_by columns."
                )
        else:
            for current_row, win in zip(res_index, ordered_windows):
                row_idx = list(win.index).index(current_row)

                start_idx = 0 if unbounded_preceding else row_idx
                end_idx = len(win) - 1 if unbounded_following else row_idx

                start_idx = search_boundary_idx(start_idx, -1, win)
                end_idx = search_boundary_idx(end_idx, 1, win)

                windows.append(win[start_idx : end_idx + 1])
    else:  # If order by is not specified, just use the entire window
        windows = res.rolling(EntireWindowIndexer())
    return windows


def handle_function_expression(
    exp: FunctionExpression,
    input_data: Union[TableEmulator, ColumnEmulator],
    analyzer: "MockAnalyzer",
    expr_to_alias: Dict[str, str],
    current_row=None,
):
    func = MockedFunctionRegistry.get_or_create().get_function(exp)
    connection_lock = analyzer.session._conn.get_lock()

    if func is None:
        with connection_lock:
            current_schema = analyzer.session.get_current_schema()
            current_database = analyzer.session.get_current_database()
        udf_name = get_fully_qualified_name(exp.name, current_schema, current_database)

        # If udf name in the registry then this is a udf, not an actual function
        with connection_lock:
            if udf_name in analyzer.session.udf._registry:
                exp.udf_name = udf_name
                return handle_udf_expression(
                    exp, input_data, analyzer, expr_to_alias, current_row
                )

        if exp.api_call_source == "functions.call_udf":
            raise SnowparkLocalTestingException(
                f"Unknown function {exp.name}. UDF by that name does not exist."
            )

        analyzer.session._conn.log_not_supported_error(
            external_feature_name=exp.name,
            error_message=f"Function {exp.name} is not implemented. You can implement and make a patch by "
            f"using the `snowflake.snowpark.mock.patch` decorator.",
            raise_error=NotImplementedError,
        )

    try:
        original_func = getattr(
            importlib.import_module("snowflake.snowpark.functions"), func.name
        )
    except AttributeError:
        original_func = None
        # raise error depending on whether users has a provided a patch for function not available in snowpark-python
        analyzer.session._conn.log_not_supported_error(
            external_feature_name=func.name,
            error_message=f"Function {func.name} is not supported in snowpark-python.",
            raise_error=NotImplementedError if func is None else None,
        )

    to_mock_func = original_func or func.impl
    # Use the non-decorated function.
    if "__wrapped__" in to_mock_func.__dict__:
        to_mock_func = to_mock_func.__wrapped__
    signatures = inspect.signature(to_mock_func)
    spec = inspect.getfullargspec(to_mock_func)
    to_pass_args = []
    type_hints = typing.get_type_hints(to_mock_func)
    parameters_except_ast = list(signatures.parameters)
    for clean_up_parameter in ["_emit_ast", "_ast"]:
        if clean_up_parameter in parameters_except_ast:
            parameters_except_ast.remove(clean_up_parameter)
            del type_hints[clean_up_parameter]
    for idx, key in enumerate(parameters_except_ast):
        type_hint = str(type_hints[key])
        keep_literal = "Column" not in type_hint
        if key == spec.varargs:
            # SNOW-1441602: Move Star logic to calculate_expression once it can handle table returns
            args = []
            for c in exp.children[idx:]:
                if isinstance(c, Star):
                    args.extend(
                        [
                            calculate_expression(
                                cc,
                                input_data,
                                analyzer,
                                expr_to_alias,
                                keep_literal=keep_literal,
                            )
                            for cc in c.expressions
                        ]
                    )
                else:
                    args.append(
                        calculate_expression(
                            c,
                            input_data,
                            analyzer,
                            expr_to_alias,
                            keep_literal=keep_literal,
                        )
                    )
            to_pass_args.extend(args)
        else:
            try:
                to_pass_args.append(
                    calculate_expression(
                        exp.children[idx],
                        input_data,
                        analyzer,
                        expr_to_alias,
                        keep_literal=keep_literal,
                    )
                )
            except IndexError:
                to_pass_args.append(None)

    try:
        result = func(*to_pass_args, row_number=current_row, input_data=input_data)
    except Exception as err:
        extra_err_info = (
            (
                f"\nA patch is provided for '{func.name}' which is not supported in Snowpark Python."
                f" Please ensure the implementation follows specifications outlined at:"
                f" https://docs.snowflake.com/en/sql-reference/functions-all and refer to"
                f" https://github.com/snowflakedb/snowpark-python/blob/main/src/snowflake/snowpark/mock/_functions.py"
                f" for patterns on creating a compatible patch function for input and output types."
            )
            if not original_func and func
            else ""
        )

        SnowparkLocalTestingException.raise_from_error(
            err,
            error_message=f"Error executing mocked function '{func.name}'."
            f" See error traceback for detailed information.{extra_err_info}",
        )

    return result


def handle_udf_expression(
    exp: FunctionExpression,
    input_data: Union[TableEmulator, ColumnEmulator],
    analyzer: "MockAnalyzer",
    expr_to_alias: Dict[str, str],
    current_row=None,
):
    udf_registry = analyzer.session.udf
    udf_name = exp.udf_name
    connection_lock = analyzer.session._conn.get_lock()
    with connection_lock:
        udf = udf_registry.get_udf(udf_name)
        udf_imports = udf_registry.get_udf_imports(udf_name)

    with ImportContext(udf_imports):
        # Resolve handler callable
        if type(udf.func) is tuple:
            module_name, handler_name = udf.func
            udf_handler = importlib.import_module(module_name).__dict__[handler_name]
        else:
            udf_handler = udf.func

        # Compute input data and validate typing
        if len(exp.children) != len(udf._input_types):
            raise SnowparkLocalTestingException(
                f"Expected {len(udf._input_types)} arguments, but received {len(exp.children)}"
            )

        function_input = TableEmulator(index=input_data.index)
        for child, expected_type in zip(exp.children, udf._input_types):
            col_name = analyzer.analyze(child, expr_to_alias)
            column_data = calculate_expression(
                child, input_data, analyzer, expr_to_alias
            )

            # Variant Data is often cast to specific python types when passed to a udf.
            if isinstance(expected_type, VariantType):
                column_data = column_data.apply(coerce_variant_input)

            coerce_result = get_coerce_result_type(
                column_data.sf_type, ColumnType(expected_type, False)
            )
            if coerce_result is None:
                raise SnowparkLocalTestingException(
                    f"UDF received input type {column_data.sf_type.datatype} for column {child.name}, but expected input type of {expected_type}"
                )

            function_input[col_name] = cast_column_to(
                column_data, ColumnType(expected_type, False)
            )

        try:
            # we do not use pd.apply here because pd.apply will auto infer dtype for the output column
            # this will lead to NaN or None information loss, think about the following case of a udf definition:
            #    def udf(x): return numpy.sqrt(x) if x is not None else None
            # calling udf(-1) and udf(None), pd.apply will infer the column dtype to be int which returns NaT for both
            # however, we want NaT for the former case and None for the latter case.
            # using dtype object + function execution does not have the limitation
            # In the future maybe we could call fix_drift_between_column_sf_type_and_dtype in methods like set_sf_type.
            # The code would look like:
            # res=input.apply(...)
            # res.set_sf_type(ColumnType(exp.datatype, exp.nullable))  # fixes the drift and removes NaT

            data = []
            for _, row in function_input.iterrows():
                if udf.strict and any([v is None for v in row]):
                    result = None
                else:
                    result = remove_null_wrapper(udf_handler(*row))
                data.append(result)

            res = ColumnEmulator(
                data=data,
                sf_type=ColumnType(exp.datatype, exp.nullable),
                name=quote_name(
                    f"{exp.udf_name}({', '.join(input_data.columns)})".upper()
                ),
                dtype=object,
            )
        except Exception as err:
            SnowparkLocalTestingException.raise_from_error(
                err, error_message=f"Python Interpreter Error: {err}"
            )

        return res


def handle_udaf_expression(
    exp: FunctionExpression,
    input_data: Union[TableEmulator, ColumnEmulator],
    analyzer: "MockAnalyzer",
    expr_to_alias: Dict[str, str],
    current_row=None,
):
    udaf_registry = analyzer.session.udaf
    udaf_name = exp.udf_name
    udaf = udaf_registry.get_udaf(udaf_name)

    with ImportContext(udaf_registry.get_udaf_imports(udaf_name)):
        # Resolve handler callable
        if type(udaf.handler) is tuple:
            module_name, handler_name = udaf.func
            udaf_class = importlib.import_module(module_name).__dict__[handler_name]
        else:
            udaf_class = udaf.handler

        # Compute input data and validate typing
        if len(exp.children) != len(udaf._input_types):
            raise SnowparkLocalTestingException(
                f"Expected {len(udaf._input_types)} arguments, but received {len(exp.children)}"
            )

        function_input = TableEmulator(index=input_data.index)
        for child, expected_type in zip(exp.children, udaf._input_types):
            col_name = analyzer.analyze(child, expr_to_alias)
            column_data = calculate_expression(
                child, input_data, analyzer, expr_to_alias
            )

            # Variant Data is often cast to specific python types when passed to a udf.
            if isinstance(expected_type, VariantType):
                column_data = column_data.apply(coerce_variant_input)

            coerce_result = get_coerce_result_type(
                column_data.sf_type, ColumnType(expected_type, False)
            )
            if coerce_result is None:
                raise SnowparkLocalTestingException(
                    f"UDAF received input type {column_data.sf_type.datatype} for column {child.name}, but expected input type of {expected_type}"
                )

            function_input[col_name] = cast_column_to(
                column_data, ColumnType(expected_type, False)
            )

        try:
            # Initialize Aggregation handler class, i.e. the aggregation accumulator.
            AggregationAccumulator = udaf_class()
            # Init its state.
            some_agg_state = AggregationAccumulator.aggregate_state

            for _, row in function_input.iterrows():
                # Call Agg.accumulate
                if udaf.strict and any([v is None for v in row]):
                    AggregationAccumulator.accumulate(None)
                else:
                    AggregationAccumulator.accumulate(*row)

            # Call merge with empty state
            AggregationAccumulator.merge(some_agg_state)
            result = AggregationAccumulator.finish()

            # Single row result for aggregation.
            res = ColumnEmulator(
                data=[result],
                sf_type=ColumnType(exp.datatype, exp.nullable),
                name=quote_name(
                    f"{exp.udf_name}({', '.join(input_data.columns)})".upper()
                ),
                dtype=object,
            )
        except Exception as err:
            SnowparkLocalTestingException.raise_from_error(
                err, error_message=f"Python Interpreter Error: {err}"
            )

        return res


def handle_udtf_expression(
    exp: FunctionExpression,
    input_data: Union[TableEmulator, ColumnEmulator],
    analyzer: "MockAnalyzer",
    expr_to_alias: Dict[str, str],
    current_row=None,
    join_with_input_columns=True,
):

    # TODO: handle and support imports + other udtf attributes.

    udtf_registry = analyzer.session.udtf
    udtf_name = exp.func_name
    udtf = udtf_registry.get_udtf(udtf_name)

    # calls __init__ in UDTF handler.
    handler = udtf.handler()

    # Vectorized or non-vectorized UDTF?
    if hasattr(handler, "end_partition") and hasattr(
        handler.end_partition, "_sf_vectorized_input"
    ):
        # vectorized
        df = input_data.copy()
        df.columns = [c.strip('"') for c in df.columns]

        data = handler.end_partition(df)

        if join_with_input_columns:
            # Join input data with output data together.
            # For now carried out as horizontal concat. Need to address join case separately.
            # suffix df accordingly, todo proper check.
            data = pd.concat(
                (df.rename(columns={c: c + "_R" for c in df.columns}), data), axis=1
            )

        return data
    else:

        res = TableEmulator(
            data=[],
        )

        output_columns = udtf._output_schema.names
        sf_types = {
            f.name: ColumnType(datatype=f.datatype, nullable=f.nullable)
            for f in udtf._output_schema.fields
        }
        sf_types_by_col_index = {
            idx: ColumnType(datatype=f.datatype, nullable=f.nullable)
            for idx, f in enumerate(udtf._output_schema.fields)
        }

        # Aliases? Use them then instead of output columns.
        # TODO SNOW-1826001: Clarify whether there will be ever a case when only some columns are aliased.
        if exp.aliases:
            output_columns = exp.aliases

        if join_with_input_columns:
            output_columns = list(input_data.columns) + output_columns

            assert len(output_columns) == len(input_data.columns) + len(
                udtf._output_schema.names
            ), "non-unique identifiers found, can't carry out table function join."

            sf_types.update(input_data.sf_types)
            sf_types_by_col_index.update(input_data.sf_types_by_col_index)

        # Process each row
        if hasattr(handler, "process"):
            data = []

            # Special case: No data, but args provided. This implies that `process` of the UDTF handler may
            # be a generator called with literals.
            if len(input_data) == 0 and exp.args:

                assert all(
                    isinstance(arg, Literal) for arg in exp.args
                ), "Arguments must be literals when no data is provided."
                args = tuple(arg.value for arg in exp.args)

                if udtf.strict:
                    data = [None]
                else:
                    result = remove_null_wrapper(handler.process(*args))
                    for result_row in result:
                        data.append(tuple(result_row))
            else:
                # input_data provided, TODO: args/kwargs.
                for _, row in input_data.iterrows():
                    if udtf.strict and any([v is None for v in row]):
                        result = None
                    else:
                        result = remove_null_wrapper(handler.process(*row))
                        for result_row in result:
                            if join_with_input_columns:
                                data.append(tuple(row.values) + tuple(result_row))
                            else:
                                data.append(tuple(result_row))

            res = TableEmulator(
                data=data,
                columns=output_columns,
                sf_types=sf_types,
                sf_types_by_col_index=sf_types_by_col_index,
            )

            res.columns = [c.strip('"') for c in res.columns]

        # Finish partition
        if hasattr(handler, "end_partition"):
            handler.end_partition()

        return res


def handle_sproc_expression(
    exp: FunctionExpression,
    input_data: Union[TableEmulator, ColumnEmulator],
    analyzer: "MockAnalyzer",
    expr_to_alias: Dict[str, str],
    current_row=None,
):
    sproc_registry = analyzer.session.sproc
    sproc_name = exp.sproc_name
    sproc = sproc_registry.get_sproc(sproc_name)

    with ImportContext(sproc_registry.get_sproc_imports(sproc_name)):
        # Resolve handler callable
        if type(sproc.func) is tuple:
            module_name, handler_name = sproc.func
            sproc_handler = importlib.import_module(module_name).__dict__[handler_name]
        else:
            sproc_handler = sproc.func

        # Compute input data and validate typing
        if len(exp.children) != len(sproc._input_types):
            raise SnowparkLocalTestingException(
                f"Expected {len(sproc._input_types)} arguments, but received {len(exp.children)}"
            )

        function_input = TableEmulator(index=input_data.index)
        for child, expected_type in zip(exp.children, sproc._input_types):
            col_name = analyzer.analyze(child, expr_to_alias)
            column_data = calculate_expression(
                child, input_data, analyzer, expr_to_alias
            )

            # Variant Data is often cast to specific python types when passed to a sproc.
            if isinstance(expected_type, VariantType):
                column_data = column_data.apply(coerce_variant_input)

            coerce_result = get_coerce_result_type(
                column_data.sf_type, ColumnType(expected_type, False)
            )
            if coerce_result is None:
                raise SnowparkLocalTestingException(
                    f"Stored procedure received input type {column_data.sf_type.datatype} for column {child.name}, but expected input type of {expected_type}"
                )

            function_input[col_name] = cast_column_to(
                column_data, ColumnType(expected_type, False)
            )

        try:
            res = sproc_handler(*function_input.values)
            if sproc._is_return_table:
                # Emulate a tabular result only if a table is returned. Else, return value is a scalar.
                output_columns = sproc._output_schema.names
                sf_types = {
                    f.name: ColumnType(datatype=f.datatype, nullable=f.nullable)
                    for f in sproc._output_schema.fields
                }
                sf_types_by_col_index = {
                    idx: ColumnType(datatype=f.datatype, nullable=f.nullable)
                    for idx, f in enumerate(sproc._output_schema.fields)
                }

                # Aliases? Use them then instead of output columns.
                # TODO SNOW-1826001: Clarify whether there will be ever a case when only some columns are aliased.
                if exp.aliases:
                    output_columns = exp.aliases
                res = TableEmulator(
                    data=res,
                    columns=output_columns,
                    sf_types=sf_types,
                    sf_types_by_col_index=sf_types_by_col_index,
                )
        except Exception as err:
            SnowparkLocalTestingException.raise_from_error(
                err, error_message=f"Python Interpreter Error: {err}"
            )

        return res


def execute_mock_plan(
    plan: MockExecutionPlan,
    expr_to_alias: Optional[Dict[str, str]] = None,
) -> Union[TableEmulator, List[Row]]:
    import numpy as np

    if expr_to_alias is None:
        expr_to_alias = plan.expr_to_alias

    if isinstance(plan, (MockExecutionPlan, SnowflakePlan)):
        source_plan = plan.source_plan
        analyzer = plan.session._analyzer
    else:
        source_plan = plan
        analyzer = plan.analyzer

    entity_registry = analyzer.session._conn.entity_registry
    connection_lock = analyzer.session._conn.get_lock()

    if isinstance(source_plan, SnowflakeValues):
        table = TableEmulator(
            source_plan.data,
            columns=[x.name for x in source_plan.output],
            sf_types={
                x.name: ColumnType(x.datatype, x.nullable) for x in source_plan.output
            },
            dtype=object,
        )
        for column_name in table.columns:
            sf_type = table.sf_types[column_name]
            table[column_name].sf_type = table.sf_types[column_name]
            if not isinstance(sf_type.datatype, _NumericType):
                table[column_name] = table[column_name].replace(np.nan, None)
        return table
    if isinstance(source_plan, MockSelectExecutionPlan):
        return execute_mock_plan(source_plan.execution_plan, expr_to_alias)
    if isinstance(source_plan, MockSelectStatement):
        projection: Optional[List[Expression]] = source_plan.projection or []
        from_: Optional[MockSelectable] = source_plan.from_
        where: Optional[Expression] = source_plan.where
        order_by: Optional[List[Expression]] = source_plan.order_by
        limit_: Optional[int] = source_plan.limit_
        offset: Optional[int] = source_plan.offset
        distinct_: bool = source_plan.distinct_
        exclude_cols: List[str] = source_plan.exclude_cols

        from_df = execute_mock_plan(from_, expr_to_alias)

        if from_df is None:
            return TableEmulator()

        columns = []
        data = []
        sf_types = []
        null_rows_idxs_map = {}
        for exp in projection:
            if isinstance(exp, Star):
                data += [d for _, d in from_df.items()]
                columns += list(from_df.columns)
                sf_types += list(
                    from_df.sf_types_by_col_index.values()
                    if from_df.sf_types_by_col_index
                    else from_df.sf_types.values()
                )
                null_rows_idxs_map.update(from_df._null_rows_idxs_map)
            elif (
                isinstance(exp, UnresolvedAlias)
                and exp.child
                and isinstance(exp.child, Star)
            ):
                for e in exp.child.expressions:
                    column_name = analyzer.analyze(e, expr_to_alias)
                    columns.append(column_name)
                    column_series = calculate_expression(
                        e, from_df, analyzer, expr_to_alias
                    )
                    data.append(column_series)
                    sf_types.append(column_series.sf_type)
                    null_rows_idxs_map[column_name] = column_series._null_rows_idxs
            else:
                if isinstance(exp, Alias):
                    column_name = expr_to_alias.get(exp.expr_id, exp.name)
                else:
                    column_name = analyzer.analyze(
                        exp, expr_to_alias, parse_local_name=True
                    )

                column_series = calculate_expression(
                    exp, from_df, analyzer, expr_to_alias
                )

                columns.append(column_name)
                data.append(column_series)
                sf_types.append(column_series.sf_type)
                null_rows_idxs_map[column_name] = column_series._null_rows_idxs

                if isinstance(exp, (Alias)):
                    if isinstance(exp.child, Attribute):
                        quoted_name = quote_name(exp.name)
                        expr_to_alias[exp.child.expr_id] = quoted_name
                        for k, v in expr_to_alias.items():
                            if v == exp.child.name:
                                expr_to_alias[k] = quoted_name

        df = pd.concat(data, axis=1)
        result_df = TableEmulator(
            data=df,
            sf_types={k: v for k, v in zip(columns, sf_types)},
            sf_types_by_col_index={i: v for i, v in enumerate(sf_types)},
        )
        result_df.columns = columns
        result_df._null_rows_idxs_map = null_rows_idxs_map

        if where:
            condition = calculate_expression(where, result_df, analyzer, expr_to_alias)
            result_df = result_df[condition.fillna(value=False)]

        if order_by:
            result_df = handle_order_by_clause(
                order_by, result_df, analyzer, expr_to_alias
            )

        if limit_ is not None:
            if offset is not None:
                result_df = result_df.iloc[offset:]
            result_df = result_df.head(n=limit_)

        if distinct_:
            result_df = result_df.drop_duplicates()

        if exclude_cols:
            result_df = result_df.drop(columns=exclude_cols)

        return result_df
    if isinstance(source_plan, MockSetStatement):
        first_operand = source_plan.set_operands[0]
        res_df = execute_mock_plan(
            MockExecutionPlan(
                first_operand.selectable,
                source_plan._session,
            ),
            expr_to_alias,
        )
        for i in range(1, len(source_plan.set_operands)):
            operand = source_plan.set_operands[i]
            operator = operand.operator
            cur_df = execute_mock_plan(
                MockExecutionPlan(operand.selectable, source_plan._session),
                expr_to_alias,
            )
            if len(res_df.columns) != len(cur_df.columns):
                raise SnowparkLocalTestingException(
                    f"SQL compilation error: invalid number of result columns for set operator input branches, expected {len(res_df.columns)}, got {len(cur_df.columns)} in branch {i + 1}"
                )
            cur_df.columns = res_df.columns
            if operator in (UNION, UNION_ALL):
                res_df = pd.concat([res_df, cur_df], ignore_index=True)
                res_df = (
                    res_df.drop_duplicates().reset_index(drop=True)
                    if operator == UNION
                    else res_df
                )
                res_df.sf_types = cur_df.sf_types
            elif operator in (EXCEPT, INTERSECT):
                # NaN == NaN evaluates to False in pandas, so we need to manually process rows that are all None/NaN
                if (
                    res_df.isnull().all(axis=1).where(lambda x: x).count() > 1
                ):  # Dedup rows that are all None/NaN
                    res_df = res_df.drop(index=res_df.isnull().all(axis=1).index[1:])

                any_null_rows_in_cur_df = cur_df.isnull().all(axis=1).any()
                null_rows_in_res_df = res_df.isnull().all(axis=1)
                if operator == INTERSECT:
                    res_df = res_df[
                        (res_df.isin(cur_df.values.ravel()).all(axis=1)).values  # IS IN
                        | (
                            any_null_rows_in_cur_df & null_rows_in_res_df.values
                        )  # Rows that are all None/NaN in both sets
                    ]
                elif operator == EXCEPT:
                    # A side-effect of Snowflake difference is that duplicates are removed from the left side
                    res_df = res_df.drop_duplicates()
                    sf_types = res_df.sf_types

                    # Two copies of the right side ensures that all rows present there are dropped when keep=False
                    res_df = pd.concat([res_df, cur_df, cur_df]).drop_duplicates(
                        keep=False
                    )
                    res_df.sf_types = sf_types

                # Compute drop duplicates
                res_df = res_df.drop_duplicates()
            else:
                analyzer.session._conn.log_not_supported_error(
                    external_feature_name=f"SetStatement operator {operator}",
                    internal_feature_name=type(source_plan).__name__,
                    parameters_info={"operator": str(operator)},
                    raise_error=NotImplementedError,
                )
        return res_df
    if isinstance(source_plan, MockSelectableEntity):
        entity_name = source_plan.entity.name
        table = entity_registry.read_table_if_exists(entity_name)
        if table is not None:
            return table

        execution_plan = entity_registry.read_view_if_exists(entity_name)
        if execution_plan is not None:
            res_df = execute_mock_plan(execution_plan, expr_to_alias)
            return res_df

        db_schema_table = parse_table_name(entity_name)
        table = ".".join([part.strip("\"'") for part in db_schema_table[:3]])
        raise SnowparkLocalTestingException(
            f"Object '{table}' does not exist or not authorized."
        )
    if isinstance(source_plan, Aggregate):
        child_rf = execute_mock_plan(source_plan.child, expr_to_alias)
        if (
            not source_plan.aggregate_expressions
            and not source_plan.grouping_expressions
        ):
            return (
                TableEmulator(child_rf.iloc[0].to_frame().T, sf_types=child_rf.sf_types)
                if len(child_rf)
                else TableEmulator(
                    data=None,
                    dtype=object,
                    columns=child_rf.columns,
                    sf_types=child_rf.sf_types,
                )
            )
        aggregate_columns = [
            plan.session._analyzer.analyze(exp, keep_alias=False)
            for exp in source_plan.aggregate_expressions
        ]
        intermediate_mapped_column = [
            f"<local_test_internal_{str(i + 1)}>" for i in range(len(aggregate_columns))
        ]
        for i in range(len(intermediate_mapped_column)):
            agg_expr = source_plan.aggregate_expressions[i]
            if isinstance(agg_expr, Alias):
                # Pop wthin group clause and reorder data if needed
                if isinstance(agg_expr.child, WithinGroup):
                    order_by_cols = coerce_order_by_arguments(
                        agg_expr.child.order_by_cols
                    )
                    child_rf = handle_order_by_clause(
                        order_by_cols, child_rf, analyzer, expr_to_alias, False
                    )
                    agg_expr = agg_expr.child

                if isinstance(agg_expr.child, Literal) and isinstance(
                    agg_expr.child.datatype, _NumericType
                ):
                    child_rf.insert(
                        len(child_rf.columns),
                        intermediate_mapped_column[i],
                        ColumnEmulator(
                            data=[agg_expr.child.value] * len(child_rf),
                            sf_type=ColumnType(
                                agg_expr.child.datatype, agg_expr.child.nullable
                            ),
                        ),
                    )
                elif isinstance(
                    agg_expr.child, (ListAgg, FunctionExpression, BinaryExpression)
                ):
                    # function expression will be evaluated later
                    child_rf.insert(
                        len(child_rf.columns),
                        intermediate_mapped_column[i],
                        ColumnEmulator(
                            data=[None] * len(child_rf),
                            dtype=object,
                            sf_type=None,  # it will be set later when evaluating the function.
                        ),
                    )
                else:
                    analyzer.session._conn.log_not_supported_error(
                        external_feature_name=f"Aggregate expression {type(agg_expr.child).__name__}",
                        internal_feature_name=type(source_plan).__name__,
                        parameters_info={
                            "agg_expr": type(agg_expr).__name__,
                            "agg_expr.child": type(agg_expr.child).__name__,
                        },
                        raise_error=NotImplementedError,
                    )
            elif isinstance(agg_expr, (Attribute, UnresolvedAlias)):
                column_name = plan.session._analyzer.analyze(agg_expr)
                try:
                    child_rf.insert(
                        len(child_rf.columns),
                        intermediate_mapped_column[i],
                        child_rf[column_name],
                    )
                except KeyError:
                    raise SnowparkLocalTestingException(
                        f"invalid identifier {column_name}"
                    )
            else:
                analyzer.session._conn.log_not_supported_error(
                    external_feature_name=f"Aggregate expression {type(agg_expr).__name__}",
                    internal_feature_name=type(source_plan).__name__,
                    parameters_info={
                        "agg_expr": type(agg_expr).__name__,
                    },
                    raise_error=NotImplementedError,
                )

        result_df_sf_Types = {}
        result_df_sf_Types_by_col_idx = {}

        column_exps = [
            (
                plan.session._analyzer.analyze(exp),
                bool(isinstance(exp, Literal)),
                calculate_expression(
                    exp, child_rf, plan.session._analyzer, expr_to_alias
                ).sf_type,
            )
            for exp in source_plan.grouping_expressions
        ]
        for idx, (column_name, _, column_type) in enumerate(column_exps):
            result_df_sf_Types[
                column_name
            ] = column_type  # TODO: fix this, this does not work
            result_df_sf_Types_by_col_idx[idx] = column_type
        # Aggregate may not have column_exps, which is allowed in the case of `Dataframe.agg`, in this case we pass
        # lambda x: True as the `by` parameter
        # also pandas group by takes None and nan as the same, so we use .astype to differentiate the two
        by_column_expression = []
        try:
            for exp in source_plan.grouping_expressions:
                if isinstance(exp, Literal) and isinstance(exp.datatype, _NumericType):
                    col_name = f"<local_test_internal_{str(exp.value)}>"
                    by_column_expression.append(child_rf[col_name])
                else:
                    column_name = plan.session._analyzer.analyze(exp)
                    if isinstance(exp, FunctionExpression):
                        materialized_column = calculate_expression(
                            exp, child_rf, plan.session._analyzer, expr_to_alias
                        )
                        # Only function expressions that are a mapping of existing columns can be aggregated on.
                        # Any increase or reduction in number of rows is an invalid function expression.
                        if len(materialized_column) == len(child_rf):
                            child_rf[column_name] = materialized_column
                    by_column_expression.append(child_rf[column_name])
        except KeyError as e:
            raise SnowparkLocalTestingException(
                f"This is not a valid group by expression due to exception {e!r}"
            )

        children_dfs = child_rf.groupby(
            by=by_column_expression or (lambda x: True), sort=False, dropna=False
        )
        # we first define the returning DataFrame with its column names
        columns = [
            quote_name(plan.session._analyzer.analyze(exp, keep_alias=False))
            for exp in source_plan.aggregate_expressions
        ]
        intermediate_mapped_column = [str(i) for i in range(len(columns))]
        result_df = TableEmulator(columns=intermediate_mapped_column, dtype=object)
        data = []

        def aggregate_by_groups(cur_group: TableEmulator):
            values = []

            if column_exps:
                for idx, (expr, is_literal, _) in enumerate(column_exps):
                    if is_literal:
                        values.append(source_plan.grouping_expressions[idx].value)
                    elif not cur_group.empty:
                        values.append(cur_group.iloc[0][expr])

            # the first len(column_exps) items of calculate_expression are the group_by column expressions,
            # the remaining are the aggregation function expressions
            for idx, exp in enumerate(
                source_plan.aggregate_expressions[len(column_exps) :]
            ):
                cal_exp_res = calculate_expression(
                    exp,
                    cur_group,
                    plan.session._analyzer,
                    expr_to_alias,
                )
                # and then append the calculated value
                if isinstance(cal_exp_res, ColumnEmulator):
                    if cur_group.size > 0 or not source_plan.grouping_expressions:
                        values.append(cal_exp_res.iat[0])
                    result_df_sf_Types[
                        columns[idx + len(column_exps)]
                    ] = result_df_sf_Types_by_col_idx[
                        idx + len(column_exps)
                    ] = cal_exp_res.sf_type
                else:
                    if cur_group.size > 0 or not source_plan.grouping_expressions:
                        values.append(cal_exp_res)
                    result_df_sf_Types[
                        columns[idx + len(column_exps)]
                    ] = result_df_sf_Types_by_col_idx[
                        idx + len(column_exps)
                    ] = ColumnType(
                        infer_type(cal_exp_res), nullable=True
                    )
            data.append(values)

        if not children_dfs.indices:
            aggregate_by_groups(child_rf)
        else:
            for _, indices in children_dfs.indices.items():
                # we construct row by row
                cur_group = child_rf.iloc[indices]
                # each row starts with group keys/column expressions, if there is no group keys/column expressions
                # it means aggregation without group (Datagrame.agg)
                aggregate_by_groups(cur_group)

        if len(data):
            for col in range(len(data[0])):
                series_data = ColumnEmulator(
                    data=[data[row][col] for row in range(len(data))],
                    dtype=object,
                )
                result_df[intermediate_mapped_column[col]] = series_data

        result_df.sf_types = result_df_sf_Types
        result_df.sf_types_by_col_index = result_df_sf_Types_by_col_idx
        result_df.columns = columns
        return result_df
    if isinstance(source_plan, Range):
        col = ColumnEmulator(
            data=[
                num
                for num in range(
                    source_plan.start, source_plan.end, int(source_plan.step)
                )
            ],
            sf_type=ColumnType(LongType(), False),
        )
        result_df = TableEmulator(
            col,
            columns=['"ID"'],
            sf_types={'"ID"': col.sf_type},
            dtype=object,
        )
        return result_df
    if isinstance(source_plan, Project):
        return TableEmulator(ColumnEmulator(col) for col in source_plan.project_list)
    if isinstance(source_plan, Join):
        L_expr_to_alias = {}
        R_expr_to_alias = {}
        left = execute_mock_plan(source_plan.left, L_expr_to_alias).reset_index(
            drop=True
        )
        right = execute_mock_plan(source_plan.right, R_expr_to_alias).reset_index(
            drop=True
        )
        # Processing ON clause
        using_columns = getattr(source_plan.join_type, "using_columns", None)
        on = using_columns
        if isinstance(on, list):  # USING a list of columns
            if on:
                on = [quote_name(x.upper()) for x in on]
            else:
                on = None
        elif isinstance(on, Column):  # ON a single column
            on = on.name
        elif isinstance(
            on, BinaryExpression
        ):  # ON a condition, apply where to a Cartesian product
            on = None
        else:  # ON clause not specified, SF returns a Cartesian product
            on = None

        # Processing the join type
        how = source_plan.join_type.sql
        if how.startswith("USING "):
            how = how[6:]
        if how.startswith("NATURAL "):
            how = how[8:]
        if how == "LEFT OUTER":
            how = "LEFT"
        elif how == "RIGHT OUTER":
            how = "RIGHT"
        elif "FULL" in how:
            how = "OUTER"
        elif "SEMI" in how:
            how = "INNER"
        elif "ANTI" in how:
            how = "CROSS"

        if (
            "NATURAL" in source_plan.join_type.sql and on is None
        ):  # natural joins use the list of common names as keys
            on = left.columns.intersection(right.columns).values.tolist()

        if on is None:
            how = "CROSS"

        result_df = left.merge(
            right,
            on=on,
            how=how.lower(),
        )

        # Restore sf_types information after merging, there should be better way to do this
        result_df.sf_types.update(left.sf_types)
        result_df.sf_types.update(right.sf_types)

        if on:
            result_df = result_df.reset_index(drop=True)
            if isinstance(on, list):
                # Reorder columns for JOINS with USING clause, where Snowflake puts the key columns to the left
                reordered_cols = on + [
                    col for col in result_df.columns.tolist() if col not in on
                ]
                result_df = result_df[reordered_cols]
                sf_types = {col: result_df.sf_types[col] for col in reordered_cols}
                result_df.sf_types = sf_types

        common_columns = set(L_expr_to_alias.keys()).intersection(
            R_expr_to_alias.keys()
        )
        new_expr_to_alias = {
            k: v
            for k, v in {
                **L_expr_to_alias,
                **R_expr_to_alias,
            }.items()
            if k not in common_columns
        }
        expr_to_alias.update(new_expr_to_alias)

        if source_plan.join_condition:

            def outer_join(base_df):
                ret = base_df.apply(tuple, 1).isin(
                    result_df[condition][base_df.columns].apply(tuple, 1)
                )
                ret.sf_type = ColumnType(BooleanType(), True)
                return ret

            condition = calculate_expression(
                source_plan.join_condition, result_df, analyzer, expr_to_alias
            ).fillna(value=False)
            sf_types = result_df.sf_types
            if "SEMI" in source_plan.join_type.sql:  # left semi
                result_df = left[outer_join(left)]
            elif "ANTI" in source_plan.join_type.sql:  # left anti
                result_df = left[~outer_join(left)]
            elif "LEFT" in source_plan.join_type.sql:  # left outer join
                # rows from LEFT that did not get matched
                unmatched_left = left[~outer_join(left)]
                unmatched_left[right.columns] = None
                result_df = pd.concat(
                    [result_df[condition], unmatched_left], ignore_index=True
                )
                for right_column in right.columns.values:
                    ct = sf_types[right_column]
                    sf_types[right_column] = ColumnType(ct.datatype, True)
            elif "RIGHT" in source_plan.join_type.sql:  # right outer join
                # rows from RIGHT that did not get matched
                unmatched_right = right[~outer_join(right)]
                unmatched_right[left.columns] = None
                result_df = pd.concat(
                    [result_df[condition], unmatched_right], ignore_index=True
                )
                for left_column in left.columns.values:
                    ct = sf_types[left_column]
                    sf_types[left_column] = ColumnType(ct.datatype, True)
            elif "OUTER" in source_plan.join_type.sql:  # full outer join
                # rows from LEFT that did not get matched
                unmatched_left = left[~outer_join(left)]
                unmatched_left[right.columns] = None
                # rows from RIGHT that did not get matched
                unmatched_right = right[~outer_join(right)]
                unmatched_right[left.columns] = None
                result_df = pd.concat(
                    [result_df[condition], unmatched_left, unmatched_right],
                    ignore_index=True,
                )
                for col_name, col_type in sf_types.items():
                    sf_types[col_name] = ColumnType(col_type.datatype, True)
            else:
                result_df = result_df[condition]
            result_df.sf_types = sf_types

        return result_df.where(result_df.notna(), None)  # Swap np.nan with None
    if isinstance(source_plan, MockFileOperation):
        return execute_file_operation(source_plan, analyzer)
    if isinstance(source_plan, SnowflakeCreateTable):
        res_df = execute_mock_plan(source_plan.query, expr_to_alias)
        return entity_registry.write_table(
            source_plan.table_name,
            res_df,
            source_plan.mode,
            column_names=source_plan.column_names,
        )
    if isinstance(source_plan, SnowflakeTable):
        entity_name = source_plan.name
        table = entity_registry.read_table_if_exists(entity_name)
        if table is not None:
            return table

        execution_plan = entity_registry.read_view_if_exists(entity_name)
        if execution_plan is not None:
            res_df = execute_mock_plan(execution_plan, expr_to_alias)
            return res_df

        obj_name_tuple = parse_table_name(entity_name)
        obj_name = obj_name_tuple[-1]

        # Logic to create a read-only temp table for AST testing purposes.
        # Functions like to_snowpark_pandas create a clone of an existing table as a read-only table that is referenced
        # during testing.
        if "SNOWPARK_TEMP_TABLE" in obj_name and "READONLY" in obj_name:
            # Create the read-only temp table.
            entity_registry.write_table(
                obj_name,
                TableEmulator({"A": [1], "B": [1], "C": [1]}),
                SaveMode.IGNORE,
            )
            return entity_registry.read_table_if_exists(obj_name)

        obj_schema = (
            obj_name_tuple[-2]
            if len(obj_name_tuple) > 1
            else analyzer.session.get_current_schema()
        )
        obj_database = (
            obj_name_tuple[-3]
            if len(obj_name_tuple) > 2
            else analyzer.session.get_current_database()
        )
        raise SnowparkLocalTestingException(
            f"Object '{obj_database[1:-1]}.{obj_schema[1:-1]}.{obj_name[1:-1]}' does not exist or not authorized."
        )
    if isinstance(source_plan, Sample):
        res_df = execute_mock_plan(source_plan.child, expr_to_alias)

        if source_plan.row_count and (
            source_plan.row_count < 0 or source_plan.row_count > 100000
        ):
            raise SnowparkLocalTestingException(
                "parameter value out of range: size of fixed sample. Must be between 0 and 1,000,000."
            )

        return res_df.sample(
            n=(
                None
                if source_plan.row_count is None
                else min(source_plan.row_count, len(res_df))
            ),
            frac=source_plan.probability_fraction,
            random_state=source_plan.seed,
        )

    if isinstance(source_plan, SampleBy):
        res_df = execute_mock_plan(source_plan.child, expr_to_alias)
        col = plan.session._analyzer.analyze(source_plan.col)
        df = reduce(
            lambda x, y: pd.concat([x, y], ignore_index=True),
            [
                res_df[res_df[col] == k].sample(frac=v)
                for k, v in source_plan.fractions.items()
            ],
        )

        df.sf_types = res_df.sf_types
        return df

    if isinstance(source_plan, CreateViewCommand):
        from_df = execute_mock_plan(source_plan.child, expr_to_alias)
        view_name = source_plan.name
        entity_registry.create_or_replace_view(
            source_plan.child, view_name, source_plan.replace
        )
        return from_df

    if isinstance(source_plan, TableUpdate):
        # since we are modifying the table, we need to ensure that no other thread
        # reads the table until it is updated
        with connection_lock:
            target = entity_registry.read_table(source_plan.table_name)
            ROW_ID = "row_id_" + generate_random_alphanumeric()
            target.insert(0, ROW_ID, range(len(target)))

            if source_plan.source_data:
                # Calculate cartesian product
                source = execute_mock_plan(source_plan.source_data, expr_to_alias)
                cartesian_product = target.merge(source, on=None, how="cross")
                cartesian_product.sf_types.update(target.sf_types)
                cartesian_product.sf_types.update(source.sf_types)
                intermediate = cartesian_product
            else:
                intermediate = target

            if source_plan.condition:
                # Select rows to be updated based on condition
                condition = calculate_expression(
                    source_plan.condition, intermediate, analyzer, expr_to_alias
                ).fillna(value=False)

                matched = target.apply(tuple, 1).isin(
                    intermediate[condition][target.columns].apply(tuple, 1)
                )
                matched.sf_type = ColumnType(BooleanType(), True)
                matched_rows = target[matched]
                intermediate = intermediate[condition]
            else:
                matched_rows = target

            # the following function is used to flatten the cell object to string which is hashable
            # as required by pandas.DataFrame.value_counts/drop_duplicates method
            def flatten_object_cell_func(cell):
                if isinstance(cell, (dict, list)):
                    return json.dumps(cell)
                return cell

            # Calculate multi_join

            # 1. count each row occurrences
            try:
                # flatten the object cell (list, dict) to string first
                flatten_intermediate = intermediate.map(flatten_object_cell_func)
            except AttributeError:  # for backward compatibility with pandas < 2.1.0
                flatten_intermediate = intermediate.applymap(flatten_object_cell_func)

            rows_value_counts = flatten_intermediate[target.columns].value_counts(
                dropna=False
            )
            # 2. get the row into tuple serving as the key to index the rows_value_counts DF
            try:
                key_index = matched_rows.map(flatten_object_cell_func).apply(tuple, 1)
            except AttributeError:  # for backward compatibility with pandas < 2.1.0
                key_index = matched_rows.applymap(flatten_object_cell_func).apply(
                    tuple, 1
                )
            matched_count = rows_value_counts[
                key_index
            ]  # 3. get the occurrences of the matched rows
            multi_joins = matched_count.where(lambda x: x > 1).count()

            # Select rows that match the condition to be updated

            # 1. get the index of the rows to update
            pd_index = flatten_intermediate.drop_duplicates(
                subset=matched_rows.columns, keep="first"
            ).index
            # 2. get the rows to update
            rows_to_update = intermediate.loc[pd_index].reset_index(
                drop=True  # ERROR_ON_NONDETERMINISTIC_UPDATE is by default False, pick one row to update
            )
            rows_to_update.sf_types = intermediate.sf_types

            # 3. Update rows in place
            for attr, new_expr in source_plan.assignments.items():
                column_name = analyzer.analyze(attr, expr_to_alias)
                target_index = target.loc[rows_to_update[ROW_ID]].index
                new_val = calculate_expression(
                    new_expr, rows_to_update, analyzer, expr_to_alias
                )
                new_val.index = target_index
                target.loc[rows_to_update[ROW_ID], column_name] = new_val

            # Delete row_id
            target = target.drop(ROW_ID, axis=1)

            # Write result back to table
            entity_registry.write_table(
                source_plan.table_name, target, SaveMode.OVERWRITE
            )
        return [Row(len(rows_to_update), multi_joins)]
    elif isinstance(source_plan, TableDelete):
        # since we are modifying the table, we need to ensure that no other thread
        # reads the table until it is updated
        with connection_lock:
            target = entity_registry.read_table(source_plan.table_name)

            if source_plan.source_data:
                # Calculate cartesian product
                source = execute_mock_plan(source_plan.source_data, expr_to_alias)
                cartesian_product = target.merge(source, on=None, how="cross")
                cartesian_product.sf_types.update(target.sf_types)
                cartesian_product.sf_types.update(source.sf_types)
                intermediate = cartesian_product
            else:
                intermediate = target

            # Select rows to keep based on condition
            if source_plan.condition:
                condition = calculate_expression(
                    source_plan.condition, intermediate, analyzer, expr_to_alias
                ).fillna(value=False)
                intermediate = intermediate[condition]
                matched = target.apply(tuple, 1).isin(
                    intermediate[target.columns].apply(tuple, 1)
                )
                matched.sf_type = ColumnType(BooleanType(), True)
                rows_to_keep = target[~matched]
            else:
                rows_to_keep = target.head(0)

            # Write rows to keep to table registry
            entity_registry.write_table(
                source_plan.table_name, rows_to_keep, SaveMode.OVERWRITE
            )
        return [Row(len(target) - len(rows_to_keep))]
    elif isinstance(source_plan, TableMerge):
        # since we are modifying the table, we need to ensure that no other thread
        # reads the table until it is updated
        with connection_lock:
            target = entity_registry.read_table(source_plan.table_name)
            ROW_ID = "row_id_" + generate_random_alphanumeric()
            SOURCE_ROW_ID = "source_row_id_" + generate_random_alphanumeric()
            # Calculate cartesian product
            source = execute_mock_plan(source_plan.source, expr_to_alias)

            # Insert row_id and source row_id
            target.insert(0, ROW_ID, range(len(target)))
            source.insert(0, SOURCE_ROW_ID, range(len(source)))

            cartesian_product = target.merge(source, on=None, how="cross")
            cartesian_product.sf_types.update(target.sf_types)
            cartesian_product.sf_types.update(source.sf_types)
            join_condition = calculate_expression(
                source_plan.join_expr, cartesian_product, analyzer, expr_to_alias
            )
            if join_condition.size == 0:
                join_condition = TableEmulator([], columns=cartesian_product.columns)
            join_result = cartesian_product[join_condition].reset_index(drop=True)
            join_result.sf_types = cartesian_product.sf_types

            # TODO [GA]: # ERROR_ON_NONDETERMINISTIC_MERGE is by default True, raise error if
            # (1) A target row is selected to be updated with multiple values OR
            # (2) A target row is selected to be both updated and deleted

            inserted_rows = []
            insert_clause_specified = (
                update_clause_specified
            ) = delete_clause_specified = False
            inserted_row_idx = set()  # source_row_id
            deleted_row_idx = set()
            updated_row_idx = set()
            for clause in source_plan.clauses:
                if isinstance(clause, UpdateMergeExpression):
                    update_clause_specified = True
                    # Select rows to update
                    if clause.condition:
                        condition = calculate_expression(
                            clause.condition, join_result, analyzer, expr_to_alias
                        ).fillna(value=False)
                        rows_to_update = join_result[condition]
                    else:
                        rows_to_update = join_result

                    rows_to_update = rows_to_update[
                        ~rows_to_update[ROW_ID]
                        .isin(updated_row_idx.union(deleted_row_idx))
                        .values
                    ]

                    # Update rows in place
                    for attr, new_expr in clause.assignments.items():
                        column_name = analyzer.analyze(attr, expr_to_alias)
                        target_index = target.loc[rows_to_update[ROW_ID]].index
                        new_val = calculate_expression(
                            new_expr, rows_to_update, analyzer, expr_to_alias
                        )
                        new_val.index = target_index
                        target.loc[rows_to_update[ROW_ID], column_name] = new_val

                    # Update updated row id set
                    for _, row in rows_to_update.iterrows():
                        updated_row_idx.add(row[ROW_ID])

                elif isinstance(clause, DeleteMergeExpression):
                    delete_clause_specified = True
                    # Select rows to delete
                    if clause.condition:
                        condition = calculate_expression(
                            clause.condition, join_result, analyzer, expr_to_alias
                        ).fillna(value=False)
                        intermediate = join_result[condition]
                    else:
                        intermediate = join_result

                    matched = target.apply(tuple, 1).isin(
                        intermediate[target.columns].apply(tuple, 1)
                    )
                    matched.sf_type = ColumnType(BooleanType(), True)

                    # Update deleted row id set
                    for _, row in target[matched].iterrows():
                        deleted_row_idx.add(row[ROW_ID])

                    # Delete rows in place
                    target = target[~matched]

                elif isinstance(clause, InsertMergeExpression):
                    insert_clause_specified = True
                    # calculate unmatched rows in the source
                    matched = source.apply(tuple, 1).isin(
                        join_result[source.columns].apply(tuple, 1)
                    )
                    matched.sf_type = ColumnType(BooleanType(), True)
                    unmatched_rows_in_source = source[~matched]

                    # select unmatched rows that qualify the condition
                    if clause.condition:
                        condition = calculate_expression(
                            clause.condition,
                            unmatched_rows_in_source,
                            analyzer,
                            expr_to_alias,
                        ).fillna(value=False)
                        unmatched_rows_in_source = unmatched_rows_in_source[condition]

                    # filter out the unmatched rows that have been inserted in previous clauses
                    unmatched_rows_in_source = unmatched_rows_in_source[
                        ~unmatched_rows_in_source[SOURCE_ROW_ID]
                        .isin(inserted_row_idx)
                        .values
                    ]

                    # update inserted row idx set
                    for _, row in unmatched_rows_in_source.iterrows():
                        inserted_row_idx.add(row[SOURCE_ROW_ID])

                    # Calculate rows to insert
                    rows_to_insert = TableEmulator(
                        [], columns=target.drop(ROW_ID, axis=1).columns, dtype=object
                    )
                    rows_to_insert.sf_types = target.sf_types
                    if clause.keys:
                        # Keep track of specified columns
                        inserted_columns = set()
                        for k, v in zip(clause.keys, clause.values):
                            column_name = analyzer.analyze(k, expr_to_alias)
                            if column_name not in rows_to_insert.columns:
                                raise SnowparkLocalTestingException(
                                    f"invalid identifier '{column_name}'"
                                )
                            inserted_columns.add(column_name)
                            new_val = calculate_expression(
                                v, unmatched_rows_in_source, analyzer, expr_to_alias
                            )
                            # pandas could do implicit type conversion, e.g. from datetime to timestamp
                            # reconstructing ColumnEmulator helps preserve the original date type
                            rows_to_insert[column_name] = ColumnEmulator(
                                new_val.values,
                                dtype=object,
                                sf_type=rows_to_insert[column_name].sf_type,
                            )

                        # For unspecified columns, use None as default value
                        for unspecified_col in set(rows_to_insert.columns).difference(
                            inserted_columns
                        ):
                            rows_to_insert[unspecified_col].replace(
                                np.nan, None, inplace=True
                            )

                    else:
                        if len(clause.values) != len(rows_to_insert.columns):
                            raise SnowparkLocalTestingException(
                                f"Insert value list does not match column list expecting {len(rows_to_insert.columns)} but got {len(clause.values)}"
                            )
                        for col, v in zip(rows_to_insert.columns, clause.values):
                            new_val = calculate_expression(
                                v, unmatched_rows_in_source, analyzer, expr_to_alias
                            )
                            rows_to_insert[col] = new_val

                    inserted_rows.append(rows_to_insert)

            # Remove inserted ROW ID column
            target = target.drop(ROW_ID, axis=1)

            # Process inserted rows
            if inserted_rows:
                res = pd.concat([target] + inserted_rows)
                res.sf_types = target.sf_types
            else:
                res = target

            # Write the result back to table
            entity_registry.write_table(source_plan.table_name, res, SaveMode.OVERWRITE)

        # Generate metadata result
        res = []
        if insert_clause_specified:
            res.append(len(inserted_row_idx))
        if update_clause_specified:
            res.append(len(updated_row_idx))
        if delete_clause_specified:
            res.append(len(deleted_row_idx))

        return [Row(*res)]
    elif isinstance(source_plan, Pivot):
        child_rf = execute_mock_plan(source_plan.child, expr_to_alias)

        assert (
            len(source_plan.aggregates) == 1
        ), "Dataframe plan should fail before this if one aggregate isn't supplied."
        agg = source_plan.aggregates[0]
        assert (
            len(agg.children) == 1
        ), "Aggregate functions should take exactly one parameter."
        agg_column = plan.session._analyzer.analyze(agg.children[0])

        agg_function_name = agg.name.lower()
        agg_functions = {
            "avg": statistics.mean,
            "count": len,
            "max": max,
            "min": min,
            "sum": sum,
        }

        if agg_function_name not in agg_functions:
            SnowparkLocalTestingException.raise_from_error(
                ValueError(
                    f"Unsupported pivot aggregation function {agg_function_name}."
                )
            )

        pivot_column = plan.session._analyzer.analyze(source_plan.pivot_column)

        if isinstance(source_plan.pivot_values, Iterable):
            pivot_values = [exp.value for exp in source_plan.pivot_values]
        elif source_plan.pivot_values is None:
            pivot_values = []
        else:
            analyzer.session._conn.log_not_supported_error(
                external_feature_name=f"Pivot values from {source_plan.pivot_values}",
                internal_feature_name=type(source_plan).__name__,
                raise_error=NotImplementedError,
            )

        # source_plan.grouping_columns contains columns specified in the groupby clause.
        # If that clause is omitted then the default behavior is to use all columns that are not
        # being used as either the agg column or the pivot column.
        # See here for more details: https://community.snowflake.com/s/article/Pivot-returns-more-rows-than-expected
        grouping_columns = [
            plan.session._analyzer.analyze(c) for c in source_plan.grouping_columns
        ]
        indices = grouping_columns or [
            col for col in child_rf.keys() if col not in {agg_column, pivot_column}
        ]

        # Missing values are filled with a sentinel object that can later be replaced with Nones
        sentinel = object()

        # Snowflake treats an empty aggregation as None, whereas pandas treats it as 0.
        # This requires us to wrap the aggregation function with extract logic to handle this special case.
        def agg_function(column):
            return (
                sentinel
                if column.isnull().all()
                else agg_functions[agg_function_name](column.dropna())
            )

        default = (
            source_plan.default_on_null.value if source_plan.default_on_null else None
        )

        # Count defaults to 0 rather than None
        if agg_function_name == "count":
            default = default or 0

        result = child_rf.pivot_table(
            columns=pivot_column,
            values=agg_column,
            aggfunc=agg_function,
            index=indices,
        )
        result.reset_index(inplace=True)

        # Select down to indices and provided values if specific values were requested
        if pivot_values:
            result = result[list(indices) + pivot_values]

        # Non-indice columns lack an sf_type, add them back in.
        for res_col in set(result.columns) - set(indices):
            # fill_na will not fill na like values with None, but it can replace them with a sentinel value
            filled = result[res_col].fillna(sentinel)

            # Sentinel values are replaced with None, then all Nones are replaced with the default if provided
            data = filled.replace({sentinel: None}).replace({None: default}).values
            # Column Emulator has to be reconctructed with sf_type in this case
            result[res_col] = ColumnEmulator(data, sf_type=child_rf[agg_column].sf_type)

            # Column name should be quoted string
            quoted_col = quote_name(str(res_col))
            result.rename(columns={res_col: quoted_col}, inplace=True)
            result.sf_types[quoted_col] = result.sf_types.pop(res_col)

        # Update column index map
        result.sf_types_by_col_index = {
            i: result[column].sf_type for i, column in enumerate(result.columns)
        }

        return result
    elif isinstance(source_plan, TableFunctionJoin):

        child_rf = execute_mock_plan(source_plan.children[0], expr_to_alias)

        # Because this is a join, need to add original columns as well.
        output = handle_udtf_expression(
            source_plan.table_function,
            child_rf,
            analyzer,
            expr_to_alias,
            join_with_input_columns=True,
        )

        return output
    elif isinstance(source_plan, SelectTableFunction):
        return handle_udtf_expression(
            plan.func_expr, [], analyzer, expr_to_alias, None, False
        )

    analyzer.session._conn.log_not_supported_error(
        external_feature_name=f"Mocking SnowflakePlan {type(source_plan).__name__}",
        internal_feature_name=type(source_plan).__name__,
        raise_error=NotImplementedError,
    )


def describe(plan: MockExecutionPlan) -> List[Attribute]:
    result = execute_mock_plan(plan)
    ret = []
    for c in result.columns:
        # Raising an exception here will cause infinite recursion
        if isinstance(result[c].sf_type.datatype, NullType):
            ret.append(
                Attribute(
                    result[c].name if result[c].name else "NULL", StringType(), True
                )
            )
        else:
            data_type = result[c].sf_type.datatype
            if isinstance(data_type, (ByteType, ShortType, IntegerType)):
                data_type = LongType()
            elif isinstance(data_type, FloatType):
                data_type = DoubleType()
            elif (
                isinstance(data_type, DecimalType)
                and data_type.precision == 38
                and data_type.scale == 0
            ):
                data_type = LongType()
            elif isinstance(data_type, StringType):
                data_type.length = (
                    data_type.length or plan.session._conn.max_string_size
                )
                data_type._is_max_size = (
                    data_type.length == plan.session._conn.max_string_size
                )

            ret.append(
                Attribute(
                    result[c].name,
                    data_type,
                    result[c].sf_type.nullable,
                )
            )
    return ret


def calculate_expression(
    exp: Expression,
    input_data: Union[TableEmulator, ColumnEmulator],
    analyzer: "MockAnalyzer",
    expr_to_alias: Dict[str, str],
    *,
    keep_literal: bool = False,
) -> ColumnEmulator:
    """
    Returns the calculated expression evaluated based on input table/column
    setting keep_literal to true returns Python datatype
    setting keep_literal to false returns a ColumnEmulator wrapping the Python datatype of a Literal
    """
    import numpy as np

    registry = MockedFunctionRegistry.get_or_create()

    if isinstance(exp, Attribute):
        try:
            return input_data[expr_to_alias.get(exp.expr_id, exp.name)]
        except KeyError:
            # expr_id maps to the projected name, but input_data might still have the exp.name
            # dealing with the KeyError here, this happens in case df.union(df)
            # TODO: check SNOW-831880 for more context
            return input_data[exp.name]
    if isinstance(exp, (UnresolvedAttribute, Attribute)):
        if exp.is_sql_text:
            analyzer.session._conn.log_not_supported_error(
                external_feature_name="SQL Text Expression",
                internal_feature_name=type(exp).__name__,
                parameters_info={"exp.is_sql_text": str(exp.is_sql_text)},
                raise_error=NotImplementedError,
            )
        try:
            return input_data[exp.name]
        except KeyError:
            try:
                return input_data[exp.name.strip('"')]
            except KeyError:
                raise SnowparkLocalTestingException(f"invalid identifier {exp.name}")
    if isinstance(exp, (UnresolvedAlias, Alias)):
        return calculate_expression(exp.child, input_data, analyzer, expr_to_alias)
    if isinstance(exp, FunctionExpression):
        return handle_function_expression(exp, input_data, analyzer, expr_to_alias)
    if isinstance(exp, ListAgg):
        lhs = calculate_expression(exp.col, input_data, analyzer, expr_to_alias)
        lhs.sf_type = ColumnType(StringType(), exp.col.nullable)
        return registry.get_function("listagg")(
            lhs,
            is_distinct=exp.is_distinct,
            delimiter=exp.delimiter,
        )
    if isinstance(exp, WithinGroup):
        order_by_cols = coerce_order_by_arguments(exp.order_by_cols)
        ordered_data = handle_order_by_clause(
            order_by_cols, input_data, analyzer, expr_to_alias, False
        )
        return calculate_expression(exp.child, ordered_data, analyzer, expr_to_alias)
    if isinstance(exp, IsNull):
        child_column = calculate_expression(
            exp.child, input_data, analyzer, expr_to_alias
        )
        res = child_column.apply(lambda x: bool(x is None))
        res.sf_type = ColumnType(BooleanType(), True)
        return res
    if isinstance(exp, IsNotNull):
        child_column = calculate_expression(
            exp.child, input_data, analyzer, expr_to_alias
        )
        res = child_column.apply(lambda x: bool(x is not None))
        res.sf_type = ColumnType(BooleanType(), True)
        return res
    if isinstance(exp, IsNaN):
        child_column = calculate_expression(
            exp.child, input_data, analyzer, expr_to_alias
        )
        res = []
        for data in child_column:
            if data is None:
                res.append(None)
            else:
                try:
                    res.append(math.isnan(data))
                except TypeError:
                    res.append(False)
        return ColumnEmulator(
            data=res, dtype=object, sf_type=ColumnType(BooleanType(), True)
        )
    if isinstance(exp, Not):
        child_column = calculate_expression(
            exp.child, input_data, analyzer, expr_to_alias
        ).astype(bool)
        return ~child_column
    if isinstance(exp, UnresolvedAttribute):
        return analyzer.analyze(exp, expr_to_alias)
    if isinstance(exp, Literal):
        if not keep_literal:
            if isinstance(exp.datatype, StringType):
                # in live session, literal of string type will have size auto inferred
                exp.datatype = StringType(len(exp.value))
            res = ColumnEmulator(
                data=[exp.value for _ in range(len(input_data))],
                sf_type=ColumnType(exp.datatype, nullable=exp.value is None),
                dtype=object,
            )
            res.index = input_data.index
            return res
        return exp.value
    if isinstance(exp, BinaryExpression):
        left = fix_drift_between_column_sf_type_and_dtype(
            calculate_expression(exp.left, input_data, analyzer, expr_to_alias)
        )
        if not isinstance(exp.right, Interval):
            right = fix_drift_between_column_sf_type_and_dtype(
                calculate_expression(exp.right, input_data, analyzer, expr_to_alias)
            )
        else:
            # INTERVAL is syntactic sugar for DATEADD.
            right = exp.right
        if isinstance(exp, Multiply):
            new_column = left * right
        elif isinstance(exp, Divide):
            new_column = left / right
        elif isinstance(exp, Add):
            if isinstance(right, Interval):
                return apply_interval(left, right, registry)
            else:
                new_column = left + right
        elif isinstance(exp, Subtract):
            if isinstance(right, Interval):
                return apply_interval(left, negate_interval(right), registry)
            new_column = left - right
        elif isinstance(exp, Remainder):
            new_column = left % right
        elif isinstance(exp, Pow):
            new_column = left**right
        elif isinstance(exp, EqualTo):
            new_column = left == right
            if left.hasnans and right.hasnans:
                new_column[
                    left.apply(lambda x: x is None) & right.apply(lambda x: x is None)
                ] = True
                new_column[
                    left.apply(lambda x: x is not None and np.isnan(x))
                    & right.apply(lambda x: x is not None and np.isnan(x))
                ] = True  # NaN == NaN evaluates to False in pandas, but True in Snowflake
                new_column[new_column.isna() | new_column.isnull()] = False
            # Special case when [1,2,3] == (1,2,3) should evaluate to True
            index = left.combine(
                right,
                lambda x, y: isinstance(x, (list, tuple))
                and isinstance(y, (list, tuple))
                and tuple(x) == tuple(y),
            )
            new_column[index] = True
        elif isinstance(exp, NotEqualTo):
            new_column = left != right
        elif isinstance(exp, GreaterThanOrEqual):
            new_column = left >= right
        elif isinstance(exp, GreaterThan):
            new_column = left > right
        elif isinstance(exp, LessThanOrEqual):
            new_column = left <= right
        elif isinstance(exp, LessThan):
            new_column = left < right
        elif isinstance(exp, And):
            new_column = (
                (left & right)
                if isinstance(input_data, TableEmulator) or not input_data
                else (left & right) & input_data
            )
        elif isinstance(exp, Or):
            new_column = (
                (left | right)
                if isinstance(input_data, TableEmulator) or not input_data
                else (left | right) & input_data
            )
        elif isinstance(exp, EqualNullSafe):
            either_isna = left.isna() | right.isna() | left.isnull() | right.isnull()
            both_isna = (left.isna() & right.isna()) | (left.isnull() & right.isnull())
            new_column = ColumnEmulator(
                [False] * len(left),
                dtype=bool,
                sf_type=ColumnType(BooleanType(), False),
            )
            new_column[either_isna] = False
            new_column[~either_isna] = left[~either_isna] == right[~either_isna]
            new_column[both_isna] = True
        elif isinstance(exp, BitwiseOr):
            new_column = left | right
        elif isinstance(exp, BitwiseXor):
            new_column = left ^ right
        elif isinstance(exp, BitwiseAnd):
            new_column = left & right
        else:
            analyzer.session._conn.log_not_supported_error(
                external_feature_name=f"Binary Expression {type(exp).__name__}",
                internal_feature_name=type(exp).__name__,
                raise_error=NotImplementedError,
            )
        return new_column
    elif isinstance(exp, ColumnSum):
        cols = [
            calculate_expression(e, input_data, analyzer, expr_to_alias)
            for e in exp.exprs
        ]
        return reduce(ColumnEmulator.add, cols)
    if isinstance(exp, UnaryMinus):
        res = calculate_expression(exp.child, input_data, analyzer, expr_to_alias)
        return -res
    if isinstance(exp, RegExp):
        lhs = calculate_expression(exp.expr, input_data, analyzer, expr_to_alias)
        raw_pattern = calculate_expression(
            exp.pattern, input_data, analyzer, expr_to_alias
        )
        flags = (
            None
            if exp.parameters is None
            else calculate_expression(
                exp.parameters, input_data, analyzer, expr_to_alias
            )
        )
        arguments = TableEmulator({"LHS": lhs, "PATTERN": raw_pattern, "FLAGS": flags})

        def _match_pattern(row) -> bool:
            input_str = row["LHS"]
            raw_pattern = row["PATTERN"]
            flag_string = row["FLAGS"]
            flags = 0

            if flag_string:
                case = multiline = newline = 0
                for c in flag_string.lower():
                    if c == "c":
                        case = 0
                    elif c == "i":
                        case = re.I
                    elif c == "m":
                        # Multi-line mode does not appear to work correctly on the server side
                        # multiline = re.M
                        pass
                    elif c == "s":
                        newline = re.S
                flags = case | multiline | newline

            _pattern = (
                f"^{raw_pattern}" if not raw_pattern.startswith("^") else raw_pattern
            )
            _pattern = f"{_pattern}$" if not _pattern.endswith("$") else _pattern

            try:
                re.compile(_pattern)
            except re.error:
                raise SnowparkLocalTestingException(
                    f"Invalid regular expression {raw_pattern}"
                )

            return bool(re.match(_pattern, input_str, flags=flags))

        result = arguments.apply(_match_pattern, axis=1)
        result.sf_type = ColumnType(BooleanType(), True)
        return result
    if isinstance(exp, Like):
        lhs = calculate_expression(exp.expr, input_data, analyzer, expr_to_alias)
        rhs = calculate_expression(exp.pattern, input_data, analyzer, expr_to_alias)
        pattern = rhs.apply(lambda x: convert_wildcard_to_regex(str(x)))
        result = pd.concat([lhs, pattern], axis=1).apply(
            lambda x: re.match(x.iloc[1], str(x.iloc[0])) is not None, axis=1
        )
        result.sf_type = ColumnType(BooleanType(), True)
        return result
    if isinstance(exp, InExpression):
        lhs = calculate_expression(exp.columns, input_data, analyzer, expr_to_alias)
        res = ColumnEmulator([False] * len(lhs), dtype=object, index=input_data.index)
        res.sf_type = ColumnType(BooleanType(), True)
        for val in exp.values:
            rhs = calculate_expression(val, input_data, analyzer, expr_to_alias)
            if isinstance(lhs, ColumnEmulator):
                if isinstance(rhs, ColumnEmulator):
                    res = res | lhs.isin(rhs)
                elif isinstance(rhs, TableEmulator):
                    res = res | lhs.isin(rhs.iloc[:, 0])
                else:
                    analyzer.session._conn.log_not_supported_error(
                        external_feature_name=f"IN expression with type {type(rhs).__name__} on the right",
                        internal_feature_name=type(exp).__name__,
                        parameters_info={"rhs": type(rhs).__name__},
                        raise_error=NotImplementedError,
                    )
            else:
                exists = lhs.apply(tuple, 1).isin(rhs.apply(tuple, 1))
                exists.sf_type = ColumnType(BooleanType(), False)
                res = res | exists
        return res
    if isinstance(exp, ScalarSubquery):
        return execute_mock_plan(exp.plan, expr_to_alias)
    if isinstance(exp, MultipleExpression):
        res = TableEmulator()
        for e in exp.expressions:
            res[analyzer.analyze(e, expr_to_alias)] = calculate_expression(
                e, input_data, analyzer, expr_to_alias
            )
        return res
    if isinstance(exp, Cast):
        column = calculate_expression(exp.child, input_data, analyzer, expr_to_alias)
        res = cast_column_to(column, ColumnType(exp.to, True), exp.try_)
        if res is None:
            analyzer.session._conn.log_not_supported_error(
                external_feature_name=f"Cast to {type(exp.to).__name__}",
                internal_feature_name=type(exp).__name__,
                parameters_info={"exp.to": type(exp.to).__name__},
                raise_error=NotImplementedError,
            )
        return res
    if isinstance(exp, CaseWhen):
        remaining = input_data
        output_data = ColumnEmulator([None] * len(input_data), index=input_data.index)
        for case in exp.branches:
            condition = calculate_expression(
                case[0], input_data, analyzer, expr_to_alias
            ).fillna(value=False)
            value = calculate_expression(case[1], input_data, analyzer, expr_to_alias)

            if output_data.sf_type is None:
                output_data.sf_type = value.sf_type
            elif any(condition) and (
                output_data.sf_type.datatype != value.sf_type.datatype
            ):
                coerce_result = get_coerce_result_type(
                    output_data.sf_type, value.sf_type
                ) or get_coerce_result_type(output_data.sf_type, value.sf_type)
                if coerce_result is None:
                    raise SnowparkLocalTestingException(
                        f"CaseWhen expressions have conflicting data types: {output_data.sf_type.datatype} != {value.sf_type.datatype}"
                    )
                else:
                    output_data = cast_column_to(output_data, coerce_result)
                    value = cast_column_to(value, coerce_result)

            true_index = remaining[condition].index
            output_data[true_index] = value[true_index]
            remaining = remaining[~remaining.index.isin(true_index)]

            if len(remaining) == 0:
                break

        if len(remaining) > 0 and exp.else_value:
            value = calculate_expression(
                exp.else_value,
                remaining.reset_index(drop=True),
                analyzer,
                expr_to_alias,
            )
            # Index was reset in order to calculate expression correctly, but needs to be in the original
            # order to replace the output data rows correctly.
            value.index = remaining.index
            if output_data.sf_type is None:
                output_data.sf_type = value.sf_type
            elif output_data.sf_type.datatype != value.sf_type.datatype:
                coerce_result = get_coerce_result_type(
                    output_data.sf_type, value.sf_type
                )
                if coerce_result is None:
                    raise SnowparkLocalTestingException(
                        f"CaseWhen expressions have conflicting data types: {output_data.sf_type.datatype} != {value.sf_type.datatype}"
                    )
                else:
                    value = cast_column_to(value, coerce_result)
            output_data[remaining.index] = value[remaining.index]

        return output_data
    if isinstance(exp, WindowExpression):
        window_function = exp.window_function
        window_spec = exp.window_spec

        # Process order by clause
        if window_spec.order_spec or isinstance(window_function, WithinGroup):
            order_spec = window_spec.order_spec
            if isinstance(window_function, WithinGroup):
                order_spec = coerce_order_by_arguments(window_function.order_by_cols)
                window_function = window_function.child

            # If the window function is a function expression then any intermediate
            # columns that are used for ordering may be needed later and should be retained.
            ordered = handle_order_by_clause(
                order_spec,
                input_data,
                analyzer,
                expr_to_alias,
                isinstance(window_function, (FunctionExpression)),
            )
        elif is_rank_related_window_function(window_function):
            raise SnowparkLocalTestingException(
                f"Window function type [{str(window_function)}] requires ORDER BY in window specification"
            )
        else:
            ordered = input_data

        res = ordered
        res_index = res.index  # List of row indexes of the result

        # Process partition_by clause
        if window_spec.partition_spec:
            # Remove duplicate keys while maintaining order
            keys = list(dict.fromkeys([exp.name for exp in window_spec.partition_spec]))
            res = res.groupby(
                keys,
                sort=False,
                as_index=False,
            )
            res_index = []
            for r in res:
                res_index += list(r[1].index)

        # Process window frame specification
        # Reference: https://docs.snowflake.com/en/sql-reference/functions-analytic#window-frame-usage-notes
        pd_index = res_index
        if not window_spec.frame_spec or not isinstance(
            window_spec.frame_spec, SpecifiedWindowFrame
        ):
            if not is_rank_related_window_function(window_function):
                windows = handle_range_frame_indexing(
                    window_spec.order_spec,
                    res_index,
                    res,
                    analyzer,
                    expr_to_alias,
                    True,
                    False,
                )

                # Pandas reindexes the data when generating rows in a RollingGroupby
                # The resulting index is not exposed in the window groupings so calculate it here
                if not isinstance(windows, list):
                    pd_index = list(windows.count().index)
            else:
                indexer = EntireWindowIndexer()
                rolling = res.rolling(indexer)
                windows = [ordered.loc[w.index] for w in rolling]
                # rolling can unpredictably change the index of the data
                # apply a trivial function to materialize the final index
                pd_index = list(rolling.count().index)

        elif isinstance(window_spec.frame_spec.frame_type, RowFrame):
            indexer = RowFrameIndexer(frame_spec=window_spec.frame_spec)
            res = res.rolling(indexer)
            res_index = list(res.count().index)
            windows = [w for w in res]

        elif isinstance(window_spec.frame_spec.frame_type, RangeFrame):
            lower = window_spec.frame_spec.lower
            upper = window_spec.frame_spec.upper
            range_bounds = None

            if isinstance(lower, (Literal, UnaryMinus, Interval)) or isinstance(
                upper, (Literal, UnaryMinus, Interval)
            ):
                if len(window_spec.order_spec) > 1:
                    raise SnowparkLocalTestingException(
                        "range_between requires exactly one order_by column."
                    )

                def get_bound(bound):
                    if isinstance(bound, Literal):
                        return bound.value
                    if isinstance(bound, CurrentRow):
                        return 0
                    if isinstance(bound, UnaryMinus):
                        return bound.child
                    if isinstance(bound, Interval):
                        return negate_interval(bound)
                    return None

                range_bounds = (get_bound(lower), get_bound(upper))

            windows = handle_range_frame_indexing(
                window_spec.order_spec,
                res_index,
                res,
                analyzer,
                expr_to_alias,
                isinstance(lower, UnboundedPreceding),
                isinstance(upper, UnboundedFollowing),
                range_bounds,
            )

        # compute window function:
        if isinstance(window_function, (FunctionExpression,)):
            res_cols = []

            for current_row, w in zip(pd_index, windows):
                result = handle_function_expression(
                    window_function, w, analyzer, expr_to_alias, current_row
                )
                result.index = [current_row]
                res_cols.append(result)

            res_col = pd.concat(res_cols) if res_cols else ColumnEmulator([])
            res_col.reindex(res_index)
            if res_cols:
                res_col.sf_type = res_cols[0].sf_type
            else:
                res_col.sf_type = ColumnType(NullType(), True)
            return res_col.sort_index()
        elif isinstance(window_function, (Lead, Lag)):
            calculated_sf_type = None
            offset = window_function.offset * (
                1 if isinstance(window_function, Lead) else -1
            )
            ignore_nulls = window_function.ignore_nulls
            res_cols = []
            for current_row, w in zip(res_index, windows):
                row_idx = list(w.index).index(
                    current_row
                )  # the row's 0-base index in the window
                offset_idx = row_idx + offset
                if offset_idx < 0 or offset_idx >= len(w):
                    sub_window_res = calculate_expression(
                        window_function.default,
                        w,
                        analyzer,
                        expr_to_alias,
                    )
                    if not calculated_sf_type:
                        calculated_sf_type = sub_window_res.sf_type
                    elif calculated_sf_type.datatype != sub_window_res.sf_type.datatype:
                        if isinstance(calculated_sf_type.datatype, NullType):
                            calculated_sf_type = sub_window_res.sf_type
                        # the result calculated upon a windows can be None, this is still valid and we can keep
                        # the calculation
                        elif not isinstance(sub_window_res.sf_type.datatype, NullType):
                            analyzer.session._conn.log_not_supported_error(
                                external_feature_name=f"Coercion of detected type"
                                f" {type(calculated_sf_type.datatype).__name__}"
                                f" and type {type(sub_window_res.sf_type.datatype).__name__}",
                                internal_feature_name=type(exp).__name__,
                                parameters_info={
                                    "window_function": type(window_function).__name__,
                                    "sub_window_res.sf_type.datatype": str(
                                        type(sub_window_res.sf_type.datatype).__name__
                                    ),
                                    "calculated_sf_type.datatype": str(
                                        type(calculated_sf_type.datatype).__name__
                                    ),
                                },
                                raise_error=SnowparkLocalTestingException,
                            )
                    res_cols.append(sub_window_res.iloc[0])
                elif not ignore_nulls or offset == 0:
                    sub_window_res = calculate_expression(
                        window_function.expr,
                        w.iloc[[offset_idx]],
                        analyzer,
                        expr_to_alias,
                    )
                    # we use the whole frame to calculate the type
                    cur_windows_sf_type = calculate_expression(
                        window_function.expr,
                        w,
                        analyzer,
                        expr_to_alias,
                    ).sf_type
                    if not calculated_sf_type:
                        calculated_sf_type = cur_windows_sf_type
                    elif calculated_sf_type != cur_windows_sf_type and (
                        not (
                            isinstance(calculated_sf_type.datatype, StringType)
                            and isinstance(cur_windows_sf_type.datatype, StringType)
                        )
                    ):
                        if isinstance(calculated_sf_type.datatype, NullType):
                            calculated_sf_type = sub_window_res.sf_type
                        # the result calculated upon a windows can be None, this is still valid and we can keep
                        # the calculation
                        elif not isinstance(sub_window_res.sf_type.datatype, NullType):
                            analyzer.session._conn.log_not_supported_error(
                                external_feature_name=f"Coercion of detected type"
                                f" {type(calculated_sf_type.datatype).__name__}"
                                f" and type {type(sub_window_res.sf_type.datatype).__name__}",
                                internal_feature_name=type(exp).__name__,
                                parameters_info={
                                    "window_function": type(window_function).__name__,
                                    "sub_window_res.sf_type.datatype": type(
                                        sub_window_res.sf_type.datatype
                                    ).__name__,
                                    "calculated_sf_type.datatype": type(
                                        calculated_sf_type.datatype
                                    ).__name__,
                                },
                                raise_error=SnowparkLocalTestingException,
                            )
                    res_cols.append(sub_window_res.iloc[0])
                else:
                    # skip rows where expr is NULL
                    delta = 1 if offset > 0 else -1
                    cur_idx = row_idx + delta
                    cur_count = 0
                    # default calc_expr is None for the case of cur_idx < 0 or cur_idx >= len(w)
                    # if cur_idx is within the value, it will be overwritten by the following valid value
                    calc_expr = ColumnEmulator(
                        [None], sf_type=ColumnType(NullType(), True), dtype=object
                    )
                    target_value = calc_expr.iloc[0]
                    while 0 <= cur_idx < len(w):
                        calc_expr = calculate_expression(
                            window_function.expr,
                            w.iloc[[cur_idx]],
                            analyzer,
                            expr_to_alias,
                        )
                        target_value = calc_expr.iloc[0]
                        if target_value is not None:
                            cur_count += 1
                            if cur_count == abs(offset):
                                break
                        cur_idx += delta
                    if not calculated_sf_type:
                        calculated_sf_type = calc_expr.sf_type
                    elif calculated_sf_type.datatype != calc_expr.sf_type.datatype:
                        if isinstance(calculated_sf_type.datatype, NullType):
                            calculated_sf_type = calc_expr.sf_type
                        # the result calculated upon a windows can be None, this is still valid and we can keep
                        # the calculation
                        elif not isinstance(  # pragma: no cover
                            calc_expr.sf_type.datatype, NullType
                        ):
                            analyzer.session._conn.log_not_supported_error(  # pragma: no cover
                                external_feature_name=f"Coercion of detected type"
                                f" {type(calculated_sf_type.datatype).__name__}"
                                f" and type {type(calc_expr.sf_type.datatype).__name__}",
                                internal_feature_name=type(exp).__name__,
                                parameters_info={
                                    "window_function": type(window_function).__name__,
                                    "calc_expr.sf_type.datatype": str(
                                        type(calc_expr.sf_type.datatype).__name__
                                    ),
                                    "calculated_sf_type.datatype": str(
                                        type(calculated_sf_type.datatype).__name__
                                    ),
                                },
                                raise_error=SnowparkLocalTestingException,
                            )
                    res_cols.append(target_value)
            res_col = ColumnEmulator(
                data=res_cols, dtype=object
            )  # dtype=object prevents implicit converting None to Nan
            res_col.index = res_index
            res_col.sf_type = (
                calculated_sf_type
                if calculated_sf_type
                else ColumnType(NullType(), True)
            )
            return res_col.sort_index()
        elif isinstance(window_function, FirstValue):
            ignore_nulls = window_function.ignore_nulls
            res_cols = []
            for w in windows:
                if not ignore_nulls:
                    res_cols.append(
                        calculate_expression(
                            window_function.expr,
                            w.iloc[[0]],
                            analyzer,
                            expr_to_alias,
                        ).iloc[0]
                    )
                else:
                    for cur_idx in range(len(w)):
                        target_expr = calculate_expression(
                            window_function.expr,
                            w.iloc[[cur_idx]],
                            analyzer,
                            expr_to_alias,
                        ).iloc[0]
                        if target_expr is not None:
                            res_cols.append(target_expr)
                            break
                    else:
                        res_cols.append(None)
            res_col = ColumnEmulator(
                data=res_cols,
                dtype=object,
                sf_type=calculate_expression(
                    window_function.expr,
                    input_data,
                    analyzer,
                    expr_to_alias,
                ).sf_type,
            )  # dtype=object prevents implicit converting None to Nan
            res_col.index = res_index
            return res_col.sort_index()
        elif isinstance(window_function, LastValue):
            ignore_nulls = window_function.ignore_nulls
            res_cols = []
            for w in windows:
                if not ignore_nulls:
                    res_cols.append(
                        calculate_expression(
                            window_function.expr,
                            w.iloc[[len(w) - 1]],
                            analyzer,
                            expr_to_alias,
                        ).iloc[0]
                    )
                else:
                    for cur_idx in range(len(w) - 1, -1, -1):
                        target_expr = calculate_expression(
                            window_function.expr,
                            w.iloc[[cur_idx]],
                            analyzer,
                            expr_to_alias,
                        ).iloc[0]
                        if target_expr is not None:
                            res_cols.append(target_expr)
                            break
                    else:
                        res_cols.append(None)
            res_col = ColumnEmulator(
                data=res_cols,
                dtype=object,
                sf_type=calculate_expression(
                    window_function.expr,
                    windows[0],
                    analyzer,
                    expr_to_alias,
                ).sf_type,
            )  # dtype=object prevents implicit converting None to Nan
            res_col.index = res_index
            return res_col.sort_index()
        else:
            analyzer.session._conn.log_not_supported_error(
                external_feature_name=f"Window Function {type(window_function).__name__}",
                internal_feature_name=type(exp).__name__,
                parameters_info={"window_function": type(window_function).__name__},
                raise_error=NotImplementedError,
            )
    elif isinstance(exp, SubfieldString):
        col = calculate_expression(exp.child, input_data, analyzer, expr_to_alias)
        field = str(exp.field)
        # in snowflake, two consecutive single quotes means escaping single quote
        field = field.replace("''", "'")
        col._null_rows_idxs = [
            index
            for index in range(len(col))
            if col[index] is not None
            and field in col[index]
            and col[index][field] is None
        ]
        res = col.apply(lambda x: None if x is None or field not in x else x[field])
        res.sf_type = ColumnType(VariantType(), col.sf_type.nullable)
        return res
    elif isinstance(exp, SubfieldInt):
        col = calculate_expression(exp.child, input_data, analyzer, expr_to_alias)
        res = col.apply(
            lambda x: None if x is None or exp.field >= len(x) else x[exp.field]
        )
        res.sf_type = ColumnType(VariantType(), col.sf_type.nullable)
        return res
    elif isinstance(exp, SnowflakeUDF):
        # Could be either UDAF or UDF, decide on type.
        if exp.is_aggregate_function:
            return handle_udaf_expression(exp, input_data, analyzer, expr_to_alias)
        else:
            return handle_udf_expression(exp, input_data, analyzer, expr_to_alias)

    analyzer.session._conn.log_not_supported_error(
        external_feature_name=f"Mocking Expression {type(exp).__name__}",
        internal_feature_name=type(exp).__name__,
        raise_error=NotImplementedError,
    )


def execute_file_operation(source_plan: MockFileOperation, analyzer: "MockAnalyzer"):
    if source_plan.operator == MockFileOperation.Operator.PUT:
        return analyzer.session._conn.stage_registry.put(
            source_plan.local_file_name, source_plan.stage_location
        )
    elif source_plan.operator == MockFileOperation.Operator.GET:
        return analyzer.session._conn.stage_registry.get(
            stage_location=source_plan.stage_location,
            target_directory=source_plan.local_file_name,
            options=source_plan.options,
        )
    elif source_plan.operator == MockFileOperation.Operator.READ_FILE:
        return analyzer.session._conn.stage_registry.read_file(
            source_plan.stage_location,
            source_plan.format,
            source_plan.schema,
            analyzer,
            source_plan.options,
        )
    analyzer.session._conn.log_not_supported_error(
        external_feature_name=f"File operation {source_plan.operator.value}"
    )
