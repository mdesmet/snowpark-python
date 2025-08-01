#
# Copyright (c) 2012-2025 Snowflake Computing Inc. All rights reserved.
#

import datetime
from decimal import Decimal

from snowflake.snowpark.types import (
    StructType,
    StructField,
    StringType,
    DecimalType,
    DoubleType,
    LongType,
    BinaryType,
    BooleanType,
    VariantType,
    DateType,
    TimestampType,
    TimeType,
    TimestampTimeZone,
)

POSTGRES_TABLE_NAME = "test_schema.ALL_TYPE_TABLE"
EXPECTED_TEST_DATA = [
    (
        -6645531000000000123,
        1,
        "0000",
        "10100101",
        False,
        "(13.68,50.87),(-14.5,-36.82)",
        b"b35feb1d048b61ac6c3",
        "6U0hXrbbRm",
        "almMRlCPh3onp9celUXb",
        "63.9.184.0/24",
        "<(-29.47,-12.75),49.48>",
        datetime.date(2004, 5, 9),
        1858055000.0,
        "5.188.71.132",
        1865101000,
        "4 years 10 mons 15 days 18:38:52",
        '{\n  "key": 123\n}',
        '{\n  "jsonb_key": 83\n}',
        "{-2.18,8.69,3.09}",
        "[(5.12,-83.91),(41.89,62.49)]",
        "e8:16:cd:a9:9f:e6",
        "e3:da:aa:fc:fb:51:86:f5",
        '"$5,452.35"',
        Decimal("113414.83"),
        "((43.79,36.77),(-64.49,-34.68))",
        "85D2538C/FFC30C2E",
        "96:207:",
        "(61.05,18.47)",
        "((48.52,53.43),(89.46,69.09),(89.54,10.13))",
        64374.96,
        -10428,
        1,
        1,
        "OaVsansivU5I1BLQdUbRaYyzbYDmK6e",
        datetime.time(19, 28, 51),
        datetime.time(8, 26, 45),
        datetime.datetime(2002, 2, 16, 2, 6),
        datetime.datetime(2020, 1, 24, 20, 0, tzinfo=datetime.timezone.utc),
        "'word3' & 'word1'",
        "'lex1':1 'lex2':2 'lex4':3",
        "10:20:10,14,15",
        "69ad9235-6c5e-4f95-b179-9730ed771aa8",
        "<root><element>34</element></root>",
    ),
    (
        -8065880000000000456,
        2,
        "1010",
        "11100010",
        False,
        "(29.78,3.39),(26.92,-5.57)",
        b"\xc3\xac4977ddf59e03da6c1e",
        "yhrAXej1DO",
        "CnwnTp8SLJKTSeQAi8oW",
        "205.202.89.0/24",
        "<(13.94,18.73),4.52>",
        datetime.date(2016, 7, 9),
        9561914000.0,
        "136.74.101.171",
        -2129554000,
        "4 years 4 mons 18:37:09",
        '{\n  "key": 123\n}',
        '{\n  "jsonb_key": 79\n}',
        "{-9.8,-0.07,0.36}",
        "[(21.72,55.15),(-25.05,94.64)]",
        "00:28:95:21:77:65",
        "58:3a:c1:f7:a1:f4:d3:31",
        '"$4,342.72"',
        Decimal("-636436.38"),
        "((-0.45,-24.74),(1.41,-82.18))",
        "D4997CEE/F6A7D891",
        "214:931:",
        "(-3.91,82.16)",
        "((21.36,22.34),(-4.94,90.88),(30.78,18.14),(25.06,60.1),(-98.04,74.43))",
        65115.97,
        32062,
        2,
        2,
        "M6Z9jw QXfvErDkIj3xYeAg0IVCTrTnWx5hS4kSu",
        datetime.time(14, 41, 19),
        datetime.time(5, 47, 38),
        datetime.datetime(2024, 12, 25, 7, 53, 11),
        datetime.datetime(2000, 8, 20, 21, 26, 46, tzinfo=datetime.timezone.utc),
        "'word2' & 'word3'",
        "'lex1':1 'lex2':2 'lex3':3",
        "701:924:720,800",
        "721f1281-9f97-4b0a-b41a-bd789ad318ea",
        "<root><element>29</element></root>",
    ),
    (
        9083626000000000789,
        3,
        "1100",
        "11000110",
        False,
        "(9.1,86.79),(-46.22,-5.63)",
        b"Rd745b97541083b56b9",
        "wTTtppE3ND",
        "mKb7K1PTNCoAdRGQHb2C",
        "145.149.241.0/24",
        "<(-91.43,-25.5),2.19>",
        datetime.date(2019, 6, 9),
        -3365294000.0,
        "11.106.208.59",
        -1889577000,
        "1 year 10 mons 23 days 21:40:12",
        '{\n  "key": null\n}',
        '{\n  "jsonb_key": 39\n}',
        "{6.62,9.28,1.6}",
        "[(-13.37,-20.05),(-96.04,-48.7)]",
        "4a:a0:71:cc:05:06",
        "dc:c8:be:f4:0c:62:69:9c",
        '"$7,161.12"',
        Decimal("261715.46"),
        "((-66.03,-52.76),(-53.48,82.21),(-99.03,7.58),(-63.51,97.87))",
        "3F5DA3B6/515C58E5",
        "86:763:",
        "(-74.55,7.85)",
        "((-6.65,69.89),(30.98,-66.15),(-96.04,-9.29))",
        -81603.21,
        -32235,
        3,
        3,
        "SWPw6w6zINbqWooVf wC90ixy0Huv9tAIbaNkTrKolY48E",
        datetime.time(7, 20, 38),
        datetime.time(20, 4),
        datetime.datetime(2017, 1, 28, 6, 29, 17),
        datetime.datetime(2009, 10, 1, 8, 16, 17, tzinfo=datetime.timezone.utc),
        "'word3' & 'word2'",
        "'lex1':2 'lex3':1 'lex4':3",
        "691:960:712,892",
        "d0164090-8196-42f0-88f6-979a4039214d",
        "<root><element>50</element></root>",
    ),
    (
        8517274000000000123,
        4,
        "1010",
        "10100111",
        False,
        "(-26.08,-37.14),(-51.59,-94.61)",
        b"n0457e0312dd983dc89",
        "8jCXO6fI1J",
        "2UENUrNFXEeMArKotkwP",
        "222.181.247.0/24",
        "<(67.48,8.88),20.47>",
        datetime.date(2012, 8, 5),
        -3428823000.0,
        "173.30.26.53",
        -495502045,
        "5 mons 25 days 21:24:37",
        '{\n  "key": null\n}',
        '{\n  "jsonb_key": 54\n}',
        "{7.34,8.92,-4.74}",
        "[(-33.09,-91.78),(-46.63,-75.51)]",
        "aa:26:f0:fb:6e:3a",
        "e0:1a:46:50:7d:b0:9f:0b",
        '"$745.17"',
        Decimal("-786274.55"),
        "((-68.79,-68.06),(-86.31,79.21),(-84.42,-97),(-80.85,-31.05),(84.56,12.15))",
        "457D6A04/B65BEF56",
        "343:454:",
        "(-27.87,-96.35)",
        "((-13.3,49.21),(88.92,57.41),(-11.51,71.87),(61.62,72.2),(57.03,-84.98),(73.6,-11.46))",
        62962.21,
        32364,
        4,
        4,
        "0wMQO5LsnVsn80kMaI 9bWKZnJJcZh VWV2cAG14Q",
        datetime.time(9, 52, 2),
        datetime.time(12, 40, 50),
        datetime.datetime(2015, 5, 26, 19, 39, 51),
        datetime.datetime(2015, 11, 14, 16, 36, 6, tzinfo=datetime.timezone.utc),
        "'word3' & 'word4'",
        "'lex2':2 'lex3':1 'lex4':3",
        "667:828:699,799",
        "45befd8f-992d-4df6-93f0-b879792d16fe",
        "<root><element>95</element></root>",
    ),
    (
        -5569903000000000456,
        5,
        "1100",
        "11110110",
        False,
        "(49.79,76.36),(-92.6,65.41)",
        b"\xc2\xb9b83bbf671ff5e2441c",
        "wOR7ebsM5d",
        "eludr6sfjh0rI1LLq73q",
        "170.29.227.0/24",
        "<(-10.95,-27.76),47.88>",
        datetime.date(2018, 4, 29),
        -6298310000.0,
        "75.135.255.85",
        310008582,
        "5 years 8 mons 5 days 00:18:31",
        '{\n  "key": "value1"\n}',
        '{\n  "jsonb_key": 37\n}',
        "{-1.37,5.98,-6.45}",
        "[(54.17,21.95),(47.55,82.92)]",
        "41:a3:38:5a:23:13",
        "4c:9e:ea:48:2b:8d:5b:39",
        '"$1,604.15"',
        Decimal("525117.11"),
        "((-55.68,-40.24),(7.22,-65.82),(88.94,-5.42))",
        "BCE30B1/AE3C727",
        "147:739:",
        "(-86.34,-55.89)",
        "((38.51,61.05),(84.17,17.95),(73.11,-73.06),(-66.17,-78.89),(21.63,-71.6),(-40.84,-70.27))",
        25392.21,
        -30587,
        5,
        5,
        "KeS36QdtwKnI9vFZ1GakjEBnNLlRpTrLEyzB",
        datetime.time(19, 35, 6),
        datetime.time(11, 21, 14),
        datetime.datetime(2005, 6, 25, 22, 2, 58),
        datetime.datetime(2017, 5, 24, 10, 3, 4, tzinfo=datetime.timezone.utc),
        "'word2' & 'word3'",
        "'lex2':2 'lex3':1 'lex4':3",
        "853:868:854,855",
        "960b86a9-a8dd-4634-bc1f-956ae6589726",
        "<root><element>47</element></root>",
    ),
    (
        None,
        6,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        6,
        6,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ),
]
postgres_schema = StructType(
    [
        StructField("BIGINT_COL", LongType(), nullable=True),
        StructField("BIGSERIAL_COL", LongType(), nullable=True),
        StructField("BIT_COL", StringType(16777216), nullable=True),
        StructField("BIT_VARYING_COL", StringType(16777216), nullable=True),
        StructField("BOOLEAN_COL", BooleanType(), nullable=True),
        StructField("BOX_COL", StringType(16777216), nullable=True),
        StructField("BYTEA_COL", BinaryType(), nullable=True),
        StructField("CHAR_COL", StringType(16777216), nullable=True),
        StructField("VARCHAR_COL", StringType(16777216), nullable=True),
        StructField("CIDR_COL", StringType(16777216), nullable=True),
        StructField("CIRCLE_COL", StringType(16777216), nullable=True),
        StructField("DATE_COL", DateType(), nullable=True),
        StructField("DOUBLE_PRECISION_COL", DoubleType(), nullable=True),
        StructField("INET_COL", StringType(16777216), nullable=True),
        StructField("INTEGER_COL", LongType(), nullable=True),
        StructField("INTERVAL_COL", StringType(16777216), nullable=True),
        StructField("JSON_COL", VariantType(), nullable=True),
        StructField("JSONB_COL", VariantType(), nullable=True),
        StructField("LINE_COL", StringType(16777216), nullable=True),
        StructField("LSEG_COL", StringType(16777216), nullable=True),
        StructField("MACADDR_COL", StringType(16777216), nullable=True),
        StructField("MACADDR8_COL", StringType(16777216), nullable=True),
        StructField("MONEY_COL", VariantType(), nullable=True),
        StructField("NUMERIC_COL", DecimalType(10, 2), nullable=True),
        StructField("PATH_COL", StringType(16777216), nullable=True),
        StructField("PG_LSN_COL", StringType(16777216), nullable=True),
        StructField("PG_SNAPSHOT_COL", StringType(16777216), nullable=True),
        StructField("POINT_COL", StringType(16777216), nullable=True),
        StructField("POLYGON_COL", StringType(16777216), nullable=True),
        StructField("REAL_COL", DoubleType(), nullable=True),
        StructField("SMALLINT_COL", LongType(), nullable=True),
        StructField("SMALLSERIAL_COL", LongType(), nullable=True),
        StructField("SERIAL_COL", LongType(), nullable=True),
        StructField("TEXT_COL", StringType(16777216), nullable=True),
        StructField("TIME_COL", TimeType(), nullable=True),
        StructField("TIME_TZ_COL", TimeType(), nullable=True),
        StructField(
            "TIMESTAMP_COL", TimestampType(TimestampTimeZone.NTZ), nullable=True
        ),
        StructField(
            "TIMESTAMPTZ_COL", TimestampType(TimestampTimeZone.TZ), nullable=True
        ),
        StructField("TSQUERY_COL", StringType(16777216), nullable=True),
        StructField("TSVECTOR_COL", StringType(16777216), nullable=True),
        StructField("TXID_SNAPSHOT_COL", StringType(16777216), nullable=True),
        StructField("UUID_COL", StringType(16777216), nullable=True),
        StructField("XML_COL", StringType(16777216), nullable=True),
    ]
)

postgres_less_column_schema = StructType(
    [
        StructField("BIGINT_COL", LongType(), nullable=True),
        StructField("BIGSERIAL_COL", LongType(), nullable=True),
        StructField("BIT_COL", StringType(16777216), nullable=True),
        StructField("BIT_VARYING_COL", StringType(16777216), nullable=True),
    ]
)

postgres_more_column_schema = StructType(
    [
        StructField("BIGINT_COL", LongType(), nullable=True),
        StructField("EXTRA_COLUMN", LongType(), nullable=True),
        StructField("BIGSERIAL_COL", LongType(), nullable=True),
        StructField("BIT_COL", StringType(16777216), nullable=True),
        StructField("BIT_VARYING_COL", StringType(16777216), nullable=True),
        StructField("BOOLEAN_COL", BooleanType(), nullable=True),
        StructField("BOX_COL", StringType(16777216), nullable=True),
        StructField("BYTEA_COL", BinaryType(), nullable=True),
        StructField("CHAR_COL", StringType(16777216), nullable=True),
        StructField("VARCHAR_COL", StringType(16777216), nullable=True),
        StructField("CIDR_COL", StringType(16777216), nullable=True),
        StructField("CIRCLE_COL", StringType(16777216), nullable=True),
        StructField("DATE_COL", DateType(), nullable=True),
        StructField("DOUBLE_PRECISION_COL", DoubleType(), nullable=True),
        StructField("INET_COL", StringType(16777216), nullable=True),
        StructField("INTEGER_COL", LongType(), nullable=True),
        StructField("INTERVAL_COL", StringType(16777216), nullable=True),
        StructField("JSON_COL", VariantType(), nullable=True),
        StructField("JSONB_COL", VariantType(), nullable=True),
        StructField("LINE_COL", StringType(16777216), nullable=True),
        StructField("LSEG_COL", StringType(16777216), nullable=True),
        StructField("MACADDR_COL", StringType(16777216), nullable=True),
        StructField("MACADDR8_COL", StringType(16777216), nullable=True),
        StructField("MONEY_COL", VariantType(), nullable=True),
        StructField("NUMERIC_COL", DecimalType(10, 2), nullable=True),
        StructField("PATH_COL", StringType(16777216), nullable=True),
        StructField("PG_LSN_COL", StringType(16777216), nullable=True),
        StructField("PG_SNAPSHOT_COL", StringType(16777216), nullable=True),
        StructField("POINT_COL", StringType(16777216), nullable=True),
        StructField("POLYGON_COL", StringType(16777216), nullable=True),
        StructField("REAL_COL", DoubleType(), nullable=True),
        StructField("SMALLINT_COL", LongType(), nullable=True),
        StructField("SMALLSERIAL_COL", LongType(), nullable=True),
        StructField("SERIAL_COL", LongType(), nullable=True),
        StructField("TEXT_COL", StringType(16777216), nullable=True),
        StructField("TIME_COL", TimeType(), nullable=True),
        StructField("TIME_TZ_COL", TimeType(), nullable=True),
        StructField(
            "TIMESTAMP_COL", TimestampType(TimestampTimeZone.NTZ), nullable=True
        ),
        StructField(
            "TIMESTAMPTZ_COL", TimestampType(TimestampTimeZone.TZ), nullable=True
        ),
        StructField("TSQUERY_COL", StringType(16777216), nullable=True),
        StructField("TSVECTOR_COL", StringType(16777216), nullable=True),
        StructField("TXID_SNAPSHOT_COL", StringType(16777216), nullable=True),
        StructField("UUID_COL", StringType(16777216), nullable=True),
        StructField("XML_COL", StringType(16777216), nullable=True),
    ]
)

postgres_unicode_schema = StructType(
    [
        StructField('"編號"', LongType(), nullable=True),
        StructField('"姓名"', StringType(16777216), nullable=True),
        StructField('"國家"', StringType(16777216), nullable=True),
        StructField('"備註"', StringType(16777216), nullable=True),
    ]
)

POSTGRES_TEST_EXTERNAL_ACCESS_INTEGRATION = "snowpark_dbapi_postgres_test_integration"
