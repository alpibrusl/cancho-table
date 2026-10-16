"""The pinned cases of the join query (docs/joins.md sections 2.3, 19 and 20, slice J3b), written by hand from the design and from the readings of section 20, not taken from a front end.

FIXTURES are the two tables the cases run on. REFUSALS are the queries the front end must refuse and where: `(query, kind, anchor, what)`; the offset is that of the anchor in the query (the first
occurrence, or the last when the anchor is `("text", "last")`, or the end of the query when it is `END`), `kind` is `syntax`, `unsupported` or `mixed`, and `what` is what was expected or the
feature. ACCEPTED are the queries that translate, with the flags they stand for and where the probe comes from. Both front ends are held to them: the reference (query_ref.py) and the tool.
"""

import query_ref as Q

FIXTURES = {
    "p.csv": "id,name,v,odd\n1,ann,10,a\n2,bob,20,b\n2,bo2,25,c\n3,cy,30,d\n5,eve,7,e\n",
    "b.csv": "id,city,n\n1,x,100\n2,y,200\n2,yy,300\n4,z,400\n",
    # columns whose names the flags must escape, and a table to join them with
    "o1.csv": 'k:1,my col,a,b,#1,-d\n1,A,x,y,p,q\n2,B,x,z,r,s\n3,C,w,z,t,u\n',
    "o2.csv": "k=2,x,y,a=b,e\\f,d\n1,P,Q,R,S,T\n2,U,V,W,X,Y\n9,Z,Z,Z,Z,Z\n",
}

END = "END"

HEAD = "select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id"
ON = "on a.id = b.id"
ALIAS_WORDS = 'a name after as (a name that is a keyword is written "like this")'
ITEM_KEYWORD = Q.WHAT_ITEM + Q.WHAT_KEYWORD
COLUMN_KEYWORD = Q.WHAT_COLUMN + Q.WHAT_KEYWORD
WHAT_BYTE = "a column, a keyword, a string or a number"
WHAT_END = "the end of the query (one statement)"
WHAT_VALUE = "a value: a number or a 'quoted string'"
GM_LIST = "the select list is the group by columns in the same order, then the aggregates"

nine = " and ".join("a.id = b.id" for _ in range(8)) + " and a.name = b.city"

REFUSALS = [
    # a column of a join is written alias.column
    ("select id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "id from", Q.WHAT_QUALIFIED),
    (HEAD + " where id = 1", "syntax", "id = 1", Q.WHAT_QUALIFIED),
    (HEAD + " where a.id = 1 and name = 'x'", "syntax", "name =", Q.WHAT_QUALIFIED),
    ("select a.id, count(*) from 'p.csv' as a join 'b.csv' as b on a.id = b.id group by id", "syntax", ("id", "last"), Q.WHAT_QUALIFIED),
    ("select count(*), sum(v) from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "v)", Q.WHAT_QUALIFIED),
    (HEAD + " order by name", "syntax", ("name", "last"), Q.WHAT_QUALIFIED),
    ("select a.name as n from 'p.csv' as a join 'b.csv' as b on a.id = b.id order by n::int", "syntax", "n::int", Q.WHAT_QUALIFIED),
    ("select a.name from 'p.csv' as a join 'b.csv' as b on id = b.id", "syntax", "id = b.id", Q.WHAT_QUALIFIED),
    ("select a.name from 'p.csv' as a join 'b.csv' as b on a.id = b.id and city = 'x'", "syntax", "city =", Q.WHAT_QUALIFIED),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id where cast(v as int) = 1", "syntax", "v as", Q.WHAT_QUALIFIED),
    ("select \"id\" from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "\"id\"", Q.WHAT_QUALIFIED),
    # an alias that is not a table's
    ("select c.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "c.id", Q.WHAT_UNKNOWN_ALIAS),
    (HEAD + " where c.id = 1", "syntax", "c.id", Q.WHAT_UNKNOWN_ALIAS),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = c.id", "syntax", "c.id", Q.WHAT_UNKNOWN_ALIAS),
    (HEAD + " group by p.id", "syntax", "p.id", Q.WHAT_UNKNOWN_ALIAS),
    ("select a.id, B.city from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "B.city", Q.WHAT_UNKNOWN_ALIAS),
    # earliest of several is the one told
    ("select a.id, c.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id where id = 1", "syntax", "c.id", Q.WHAT_UNKNOWN_ALIAS),
    ("select id, c.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "id,", Q.WHAT_QUALIFIED),
    # a position, a star, what is after the dot
    ("select #1 from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "unsupported", "#1", Q.F_POSITION),
    ("select a.#2 from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "unsupported", "#2", Q.F_POSITION),
    (HEAD + " where #1 = 'x'", "unsupported", "#1", Q.F_POSITION),
    ("select a.* from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "unsupported", "*", Q.F_STAR),
    ("select a.id, b.* from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "unsupported", "*", Q.F_STAR),
    ("select a.( from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "( from", Q.WHAT_AFTER_DOT),
    ("select a. from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "from 'p", ITEM_KEYWORD),
    ("select a.order from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "syntax", "order", ITEM_KEYWORD),
    (HEAD + " where a.and = 1", "syntax", "and =", COLUMN_KEYWORD),
    # the head of a join
    ("select a.id from 'p.csv' join 'b.csv' on a.id = b.id", "syntax", ".", WHAT_BYTE),
    ("select id from 'p.csv' join 'b.csv' on id = id", "unsupported", "join", "join"),
    ("select id from 'p.csv' left join 'b.csv' on id = id", "unsupported", "left", "join"),
    ("select id from 'p.csv' as a semi 'b.csv'", "syntax", "as a", WHAT_END),
    ("select id from 'p.csv' as a semi join 'b.csv'", "syntax", "id from", Q.WHAT_QUALIFIED),
    ("select a.id from 'p.csv' a join 'b.csv' b on a.id = b.id", "syntax", ".", WHAT_BYTE),
    ("select a.id from 'p.csv' as a join 'b.csv' b on a.id = b.id", "syntax", "b on", Q.WHAT_AS_TABLE),
    ("select a.id from 'p.csv' as a join 'b.csv' on a.id = b.id", "syntax", "on a.id", Q.WHAT_AS_TABLE),
    ("select a.id from 'p.csv' as a join 'b.csv' as on a.id = a.id", "syntax", "on a.id", ALIAS_WORDS),
    ("select a.id from 'p.csv' as a join 'b.csv' as 5 on a.id = a.id", "syntax", "5", ALIAS_WORDS),
    ("select a.id from 'p.csv' as a join 'b.csv' as a on a.id = a.id", "syntax", "a on", Q.WHAT_ALIAS_SAME),
    ("select x.id from 'p.csv' as \"x\" join 'b.csv' as x on x.id = x.id", "syntax", "x on", Q.WHAT_ALIAS_SAME),
    ("select a.id from 'p.csv' as a join 'b.csv' as b where a.id = 1", "syntax", "where", Q.WHAT_ON),
    ("select a.id from 'p.csv' as a join 'b.csv' as b", "syntax", END, Q.WHAT_ON),
    ("select a.id from 'p.csv' as a join 'b.csv' as b using (id)", "syntax", "using", Q.WHAT_ON),
    ("select a.id from 'p.csv' as a join b.csv as b on a.id = b.id", "syntax", "b.csv", Q.WHAT_FILE2),
    ("select a.id from 'p.csv' as a join 5 as b on a.id = b.id", "syntax", "5", Q.WHAT_FILE2),
    ("select a.id from 'p.csv' as a join \"b.csv\" as b on a.id = b.id", "syntax", "\"b.csv\"", Q.WHAT_FILE2),
    ("select a.id from 'p.csv' as a inner 'b.csv' as b on a.id = b.id", "syntax", "'b.csv'", Q.WHAT_JOIN_WORD),
    ("select a.id from 'p.csv' as a left 'b.csv' as b on a.id = b.id", "syntax", "'b.csv'", Q.WHAT_JOIN_WORD),
    ("select a.id from 'p.csv' as a left join", "syntax", END, Q.WHAT_FILE2),
    ("select a.id from 'p.csv' as a join", "syntax", END, Q.WHAT_FILE2),
    # the joins that are not offered
    ("select a.id from 'p.csv' as a right join 'b.csv' as b on a.id = b.id", "unsupported", "right", Q.F_RIGHT),
    ("select a.id from 'p.csv' as a full join 'b.csv' as b on a.id = b.id", "unsupported", "full", Q.F_FULL),
    ("select a.id from 'p.csv' as a cross join 'b.csv' as b on a.id = b.id", "unsupported", "cross", Q.F_CROSS),
    ("select a.id from 'p.csv' as a left outer join 'b.csv' as b on a.id = b.id", "unsupported", "outer", Q.F_OUTER),
    ("select a.id from 'p.csv' as a right outer join 'b.csv' as b on a.id = b.id", "unsupported", "right", Q.F_RIGHT),
    ("select a.id from stdin as a join stdin as b on a.id = b.id", "unsupported", ("stdin", "last"), Q.F_STDIN_BUILD),
    ("select a.id from 'p.csv' as a join stdin as b on a.id = b.id", "unsupported", "stdin", Q.F_STDIN_BUILD),
    (HEAD + " join 'c.csv' as c on c.id = a.id", "unsupported", "join 'c", Q.F_MORE),
    (HEAD + " left join 'c.csv' as c on c.id = a.id", "unsupported", "left", Q.F_MORE),
    (HEAD + " semi join 'c.csv' as c on c.id = a.id", "unsupported", "semi", Q.F_MORE),
    (HEAD + " cross join 'c.csv' as c", "unsupported", "cross", Q.F_MORE),
    # the key equalities
    ("select a.id from 'p.csv' as a join 'b.csv' as b on b.city = 'x'", "syntax", "b.city", Q.WHAT_FIRST_EQ),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on (a.id = b.id)", "syntax", "(a.id", Q.WHAT_FIRST_EQ),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on not a.id = b.id", "syntax", "not", Q.WHAT_FIRST_EQ),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = 5", "syntax", "a.id = 5", Q.WHAT_FIRST_EQ),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id", "syntax", ("a.id", "last"), Q.WHAT_FIRST_EQ),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = a.name", "syntax", "a.name", Q.WHAT_EQ_SIDES),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on b.id = b.city", "syntax", "b.city", Q.WHAT_EQ_SIDES),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id::int = b.id", "syntax", ("b.id", "last"), Q.WHAT_EQ_TYPES),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id::int", "syntax", "b.id", Q.WHAT_EQ_TYPES),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id::int = b.id::float", "syntax", "b.id", Q.WHAT_EQ_TYPES),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id::dec(2) = b.id::dec(3)", "syntax", "b.id", Q.WHAT_EQ_TYPES),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on cast(a.id as int) = b.id", "syntax", "b.id", Q.WHAT_EQ_TYPES),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on " + nine, "syntax", ("a.name", "last"), Q.WHAT_PAIRS),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and b.city = 'x' and a.v = b.n", "syntax", "a.v = b.n", Q.WHAT_EQ_LATE),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id or b.city = 'x'", "unsupported", "or b.city", Q.F_OR_ON),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and b.city = 'x' or b.n = 1", "unsupported", "or b.n", Q.F_OR_ON),
    # a condition of the on
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and a.v = 1", "unsupported", "a.v", Q.F_LEFT_IN_ON),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and (b.n = 1 or a.v = 1)", "unsupported", "a.v", Q.F_LEFT_IN_ON),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and not a.v = 1", "unsupported", "a.v", Q.F_LEFT_IN_ON),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and (b.city = 'x' and b.n = 1 or b.n = 2)", "mixed", "or b.n = 2", None),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and b.city like 'x%'", "unsupported", "like", "like"),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and b.n in ()", "syntax", ")", WHAT_VALUE),
    # a semi or anti join gives the left table's columns only
    ("select b.city from 'p.csv' as a semi join 'b.csv' as b on a.id = b.id", "unsupported", "b.city", Q.F_RIGHT_OF_SEMI),
    ("select a.id from 'p.csv' as a anti join 'b.csv' as b on a.id = b.id where b.n = 1", "unsupported", "b.n", Q.F_RIGHT_OF_SEMI),
    ("select a.id, count(*) from 'p.csv' as a semi join 'b.csv' as b on a.id = b.id group by a.id order by b.n", "unsupported", "b.n", Q.F_RIGHT_OF_SEMI),
    ("select a.id from 'p.csv' as a semi join 'b.csv' as b on a.id = b.id order by b.id", "unsupported", ("b.id", "last"), Q.F_RIGHT_OF_SEMI),
    # what the other clauses refuse, as ever
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id where a.v = 1 and a.id = 2 or a.name = 'x'", "mixed", "or a.name", None),
    ("select a.id, count(*) from 'p.csv' as a join 'b.csv' as b on a.id = b.id", "group-mismatch", "a.id, count", GM_LIST),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id group by b.city", "group-mismatch", "a.id from", GM_LIST),
    ("select a.id, count(*) from 'p.csv' as a join 'b.csv' as b on a.id = b.id group by a.id order by b.city", "group-mismatch", "b.city", "a group by column or an aggregate of the select list"),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id group by a.id::int", "unsupported", "a.id::int", "cast-in-group"),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id where a.id = b.id", "unsupported", ("b.id", "last"), "column-comparison"),
]


def offset_of(query, anchor):
    if anchor == END:
        return len(query)
    if isinstance(anchor, tuple):
        return query.rindex(anchor[0])
    return query.index(anchor)


ACCEPTED = [
    ("select a.id, b.city from 'p.csv' as a join 'b.csv' as b on a.id = b.id",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--select", "id,b.city"], ("file", "p.csv")),
    ("select * from 'p.csv' as a left join 'b.csv' as b on a.id = b.id",
     ["--rows", "--join", "b.csv", "--on", "id=id", "--join-type", "left", "--join-prefix", "b."], ("file", "p.csv")),
    ("select a.name from 'p.csv' as a semi join 'b.csv' as b on b.id = a.id and b.n > 150",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "semi", "--join-where", "n > 150", "--select", "name"], ("file", "p.csv")),
    ("select a.id from 'p.csv' as a anti join 'b.csv' as b on a.id::int = b.id::int where a.v::int >= 10 order by a.v::int desc, a.id limit 2 offset 1",
     ["--join", "b.csv", "--on", "id=id:int", "--join-type", "anti", "--select", "id", "--where", "v:int >= 10", "--order-by", "-v:int,id", "--limit", "2", "--from", "1"], ("file", "p.csv")),
    ("select a.name, count(*) as n, sum(b.n) from 'p.csv' as a join 'b.csv' as b on a.id = b.id group by a.name order by n desc",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--group", "name", "--agg", "count,sum:b.n", "--sort", "-n", "--rename", "#2=n"], ("file", "p.csv")),
    ("select a.id from stdin as a join 'b.csv' as b on a.id = b.id",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--select", "id"], ("stdin", None)),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and a.name = b.city",
     ["--join", "b.csv", "--on", "id=id,name=city", "--join-type", "inner", "--join-prefix", "b.", "--select", "id"], ("file", "p.csv")),
    ("SELECT a.id FROM 'p.csv' AS a INNER JOIN 'b.csv' AS b ON a . id = b . id",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--select", "id"], ("file", "p.csv")),
    ("select y.city from 'p.csv' as x join 'b.csv' as y on x.id = y.id",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "y.", "--select", "y.city"], ("file", "p.csv")),
    ("select \"b t\".city from 'p.csv' as \"p t\" join 'b.csv' as \"b t\" on \"p t\".id = \"b t\".id",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b t.", "--select", "b t.city"], ("file", "p.csv")),
    ("select a.id from 'p.csv' as a semi join 'b.csv' as b on a.id = b.id where a.name != 'x'",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "semi", "--select", "id", "--where", "name != 'x'"], ("file", "p.csv")),
    ("select a.id from 'p.csv' as a left join 'b.csv' as b on a.id = b.id and (b.city = 'x' or not (b.n > 5))",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "left", "--join-prefix", "b.", "--join-where", "(city = 'x' or not (n > 5))", "--select", "id"], ("file", "p.csv")),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on b.id = a.id where b.city in ('x', 'y') and a.v::int > 5 order by b.city",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--select", "id", "--where", "'b.city' in ('x', 'y') and v:int > 5", "--order-by", "b.city"], ("file", "p.csv")),
    ("select a.name as who, b.city as town from 'p.csv' as a left join 'b.csv' as b on a.id = b.id order by town, who desc",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "left", "--join-prefix", "b.", "--select", "name,b.city", "--order-by", "b.city,-name", "--rename", "#1=who,#2=town"], ("file", "p.csv")),
    ("select a.odd, count(distinct b.city), max(a.v) from 'p.csv' as a join 'b.csv' as b on a.id = b.id group by a.odd order by a.odd desc",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--group", "odd", "--agg", "distinct:b.city,max:v", "--sort", "-odd"], ("file", "p.csv")),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and b.n = '200' limit 5",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--join-where", "n = '200'", "--select", "id", "--limit", "5"], ("file", "p.csv")),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id offset 2",
     ["--join", "b.csv", "--on", "id=id", "--join-type", "inner", "--join-prefix", "b.", "--select", "id", "--from", "2"], ("file", "p.csv")),
]

# columns whose names need escaping in the flags: the query writes them "quoted", the flags escape them in their own ways
ODD = [
    ("select a.\"my col\", b.\"a=b\", b.\"e\\f\" from 'o1.csv' as a join 'o2.csv' as b on a.\"k:1\" = b.\"k=2\"",
     ["--join", "o2.csv", "--on", "k\\:1=k\\=2", "--join-type", "inner", "--join-prefix", "b.", "--select", "my col,b.a=b,b.e\\\\f"], ("file", "o1.csv")),
    ("select a.\"#1\", a.\"-d\", b.d from 'o1.csv' as a left join 'o2.csv' as b on a.\"k:1\" = b.\"k=2\" order by a.\"-d\" desc",
     ["--join", "o2.csv", "--on", "k\\:1=k\\=2", "--join-type", "left", "--join-prefix", "b.", "--select", "\\#1,-d,b.d", "--order-by", "-\\-d"], ("file", "o1.csv")),
    ("select b.x, count(*) from 'o1.csv' as a join 'o2.csv' as b on a.\"k:1\" = b.\"k=2\" group by b.x",
     ["--join", "o2.csv", "--on", "k\\:1=k\\=2", "--join-type", "inner", "--join-prefix", "b.", "--group", "b.x", "--agg", "count"], ("file", "o1.csv")),
]


# A literal of a condition (in where, or in the on of a join) that does not fit the type its column is read as is told as query.syntax at the literal of the query
# (the query, the literal as written, the column's own name, the type, what the flag's parser expected of it)
LITERALS = [
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id and b.n::int = 1.5", "1.5", "n", "int", "an integer, because the column is :int"),
    ("select a.id from 'p.csv' as a left join 'b.csv' as b on a.id = b.id and b.n::dec(1) > 1.25", "1.25", "n", "dec(1)",
     "a decimal with no more fractional digits than the column's scale (write :dec(N) with a larger N to compare finer values)"),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id where a.v::int in (1, 'z')", "'z'", "v", "int", "an integer, because the column is :int"),
    ("select a.id from 'p.csv' as a join 'b.csv' as b on a.id = b.id where cast(b.\"x y\" as int) = 1.5", "1.5", "x y", "int", "an integer, because the column is :int"),
]
