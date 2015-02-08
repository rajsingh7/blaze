from __future__ import absolute_import, print_function, division

import pytest

xfail = pytest.mark.xfail

pytest.importorskip('pyspark')
pytest.importorskip('pyspark.sql')
sa = pytest.importorskip('sqlalchemy')

import pandas as pd
import pandas.util.testing as tm
from datashape.predicates import iscollection
from datashape import dshape
from blaze import discover, compute, symbol, into, by, sin, exp, join
from pyspark.sql import SQLContext, Row, DataFrame as SparkDataFrame
from into.utils import tmpfile


data = [['Alice', 100.0, 1],
        ['Bob', 200.0, 2],
        ['Alice', 50.0, 3]]

cities_data = [['Alice', 'NYC'],
               ['Bob', 'Boston']]

df = pd.DataFrame(data, columns=['name', 'amount', 'id'])
cities_df = pd.DataFrame(cities_data, columns=['name', 'city'])


# sc is from conftest.py


@pytest.fixture(scope='module')
def sql(sc):
    return SQLContext(sc)


@pytest.yield_fixture(scope='module')
def people(sc):
    with tmpfile('.txt') as fn:
        df.to_csv(fn, header=False, index=False)
        raw = sc.textFile(fn)
        parts = raw.map(lambda line: line.split(','))
        yield parts.map(lambda person: Row(name=person[0],
                                           amount=float(person[1]),
                                           id=int(person[2])))


@pytest.yield_fixture(scope='module')
def cities(sc):
    with tmpfile('.txt') as fn:
        cities_df.to_csv(fn, header=False, index=False)
        raw = sc.textFile(fn)
        parts = raw.map(lambda line: line.split(','))
        yield parts.map(lambda person: Row(name=person[0],
                                           city=person[1]))


@pytest.fixture(scope='module')
def ctx(sql, people, cities):
    schema = sql.inferSchema(people)
    schema.registerTempTable('t')

    schema = sql.inferSchema(cities)
    schema.registerTempTable('s')
    return sql


@pytest.fixture(scope='module')
def db(ctx):
    return symbol('db', discover(ctx))


@xfail(reason='moving to into')
def test_into_SparkSQL_from_PySpark(db, ctx):
    srdd = into(ctx, data, schema=db.t.dshape)
    assert isinstance(srdd, SparkDataFrame)

    # assert into(list, rdd) == into(list, srdd)


@xfail(reason='moving to into')
def test_into_sparksql_from_other(ctx):
    srdd = into(ctx, df)
    assert isinstance(srdd, SparkDataFrame)
    assert into(list, srdd) == into(list, df)


@xfail(reason='moving to into')
def test_discover(db, ctx):
    srdd = into(ctx, data, schema=db.t.schema)
    assert discover(srdd).subshape[0] == \
        dshape('{name: string, amount: int64, id: int64}')


def test_projection(db, ctx):
    expr = db.t[['id', 'name']]
    result = compute(expr, ctx)
    expected = compute(expr, {db: {'t': df}})
    assert into(set, result) == into(set, expected)


def test_symbol_compute(db, ctx):
    assert isinstance(compute(db.t, ctx), SparkDataFrame)


def test_field_access(db, ctx):
    expr = db.t.name
    expected = compute(expr, ctx)
    result = compute(expr, {db: {'t': df}})
    tm.assert_series_equal(into(pd.Series, expected), result)


def test_head(db, ctx):
    expr = db.t[['name', 'amount']].head(2)
    result = compute(expr, ctx)
    expected = compute(expr, {db: {'t': df}})
    assert into(list, result) == into(list, expected)


def test_literals(db, ctx):
    expr = db.t[db.t.amount >= 100]
    result = compute(expr, ctx)
    expected = compute(expr, {db: {'t': df}})
    assert list(map(set, into(list, result))) == \
        list(map(set, into(list, expected)))


def test_by_summary(db, ctx):
    t = db.t
    expr = by(t.name, mymin=t.amount.min(), mymax=t.amount.max())
    result = compute(expr, ctx)
    expected = compute(expr, {db: {'t': df}})
    assert into(set, result) == into(set, expected)


def test_join(db, ctx):
    expr = join(db.t, db.s)
    result = compute(expr, ctx)
    expected = compute(expr, {db: {'t': df, 's': cities_df}})

    assert isinstance(result, SparkDataFrame)
    assert into(set, result) == into(set, expected)
    assert discover(result) == expr.dshape


@xfail(reason='not worked out yet')
def test_comprehensive(sc, ctx, db):
    L = [[100, 1, 'Alice'],
         [200, 2, 'Bob'],
         [300, 3, 'Charlie'],
         [400, 4, 'Dan'],
         [500, 5, 'Edith']]

    df = pd.DataFrame(L, columns=['amount', 'id', 'name'])

    rdd = into(sc, df)
    srdd = into(ctx, df)

    t = db.t

    expressions = {
        t: [],
        t['id']: [],
        t.id.max(): [],
        t.amount.sum(): [],
        t.amount + 1: [],
        # sparksql without hiveql doesn't support math
        sin(t.amount): [srdd],
        # sparksql without hiveql doesn't support math
        exp(t.amount): [srdd],
        t.amount > 50: [],
        t[t.amount > 50]: [],
        t.sort('name'): [],
        t.sort('name', ascending=False): [],
        t.head(3): [],
        t.name.distinct(): [],
        t[t.amount > 50]['name']: [],
        t.id.map(lambda x: x + 1, 'int'): [srdd],  # no udfs yet
        t[t.amount > 50]['name']: [],
        by(t.name, total=t.amount.sum()): [],
        by(t.id, total=t.id.count()): [],
        by(t[['id', 'amount']], total=t.id.count()): [],
        by(t[['id', 'amount']], total=(t.amount + 1).sum()): [],
        by(t[['id', 'amount']], total=t.name.nunique()): [rdd, srdd],
        by(t.id, total=t.amount.count()): [],
        by(t.id, total=t.id.nunique()): [rdd, srdd],
        # by(t, t.count()): [],
        # by(t.id, t.count()): [df],
        t[['amount', 'id']]: [],
        t[['id', 'amount']]: [],
    }

    for e, exclusions in expressions.items():
        if rdd not in exclusions:
            if iscollection(e.dshape):
                assert into(set, compute(e, rdd)) == into(
                    set, compute(e, df))
            else:
                assert compute(e, rdd) == compute(e, df)
        if srdd not in exclusions:
            if iscollection(e.dshape):
                assert into(set, compute(e, srdd)) == into(
                    set, compute(e, df))
            else:
                assert compute(e, rdd) == compute(e, df)
