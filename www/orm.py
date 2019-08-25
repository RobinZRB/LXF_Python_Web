# 一处异步，处处异步
import asyncio, logging, aiomysql


def log(sql, args=()):
    logging.info('SQL: %s' % sql)


# 我们需要创建一个全局的连接池，每个HTTP请求都可以从连接池中直接获取数据库连接。使用连接池的好处是不必频繁地打开和关闭数据库连接，而是能复用就尽量复用
# **kw表示传入的是不限长度的dict（这里应该传入config里的db）
async def create_pool(loop, **kw):
    logging.info('create database connection pool...')
    global __pool
    # 调用aiomysql中的方法创建连接池并设定初始属性
    # dict有一个get方法，如果dict中有对应的value值，则返回对应于key的value值，否则返回默认值，例如下面的host，如果dict里面没有
    # 'host',则返回后面的默认值，也就是'localhost'
    __pool = await aiomysql.create_pool(
        # 连接池的初始化数据
        host=kw.get('host', 'localhost'),
        port=kw.get('port', 3306),
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],
        charset=kw.get('charset', 'utf8'),
        autocommit=kw.get('autocommit', True),
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),
        loop=loop
    )


# 要执行SELECT语句，我们用select函数执行，需要传入SQL语句和SQL参数
async def select(sql, args, size=None):
    log(sql, args)
    global __pool
    # 在连接池中建立一个数据库连接
    with (await __pool) as conn: # 使用该语句的前提是已经创建了进程池，因为这句话是在函数定义里面，所以可以这样用
        # 定义连接的指针
        cur = await conn.cursor(aiomysql.DictCursor)
        # SQL语句的占位符是?，而MySQL的占位符是%s，select()函数在内部自动替换
        await cur.execute(sql.replace('?', '%s'), args or ())
        if size:
            rs = await cur.fetchmany(size) # 一次性返回size条查询结果，结果是一个list，里面是tuple（findNumber）
        else:
            rs = await cur.fetchall() # 一次性返回所有的查询结果（findAll）
        # 关闭数据库连接
        await cur.close() # 关闭游标，不用手动关闭conn，因为是在with语句里面，会自动关闭，因为是select，所以不需要提交事务(commit)
        logging.info('rows returned: %s' % len(rs))
        return rs # 返回查询结果，元素是tuple的list


# 要执行INSERT、UPDATE、DELETE语句，可以定义一个通用的execute()函数，因为这3种SQL的执行都需要相同的参数，以及返回一个整数表示影响的行数
# execute()函数和select()函数所不同的是，cursor对象不返回结果集，而是通过rowcount返回结果数
async def execute(sql, args):
    log(sql)
    with (await __pool) as conn:
        try:
            # 因为execute类型sql操作返回结果只有行号，不需要dict
            cur = await conn.cursor()
            # 和上面同理，执行占位符转换
            await cur.execute(sql.replace('?', '%s'), args)
            # 影响的行动数
            affected = cur.rowcount
            await cur.close()
        except BaseException as e:
            raise
        # 如果affected等于1，表示操作成功
        return affected


# 注意到Model只是一个基类，要将具体的子类如User的映射信息读取出来需要通过metaclass：ModelMetaclass
# 这样，任何继承自Model的类（比如User），会自动通过ModelMetaclass扫描映射关系，并存储到自身的类属性如__table__、__mappings__中
# 然后，我们往Model类添加class方法，就可以让所有子类调用class方法
def create_args_string(num):
    # 参数数量，目的是与占位符数量匹配，防止报错
    L = []
    for n in range(num):
        L.append('?')
    return ', '.join(L)


# 创建所有ORM框架的元类，使得以元类的方法创造实例（获得映射关系）
class ModelMetaclass(type):

    def __new__(cls, name, bases, attrs):
        # 排除对Model类本身的修改:
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        # 获取table名称:（类是先生成，再被元类进行修改，所以这里是能够得到表名的）
        tableName = attrs.get('__table__', None) or name # r如果存在表名，则返回表名，否则返回name
        logging.info('found model: %s (table: %s)' % (name, tableName))
        # 获取所有的Field和主键名:
        mappings = dict()
        fields = [] # field保存的是除主键外的属性名
        primaryKey = None
        for k, v in attrs.items():
            if isinstance(v, Field):
                logging.info('  found mapping: %s ==> %s' % (k, v))
                mappings[k] = v
                if v.primary_key:
                    # 找到主键:
                    if primaryKey:
                        #一个表只能有一个主键，当再出现一个主键的时候就报错
                        raise RuntimeError('Duplicate primary key for field: %s' % k)
                    # 也就是说主键只能被设置一次
                    primaryKey = k
                else:
                    fields.append(k)
        if not primaryKey:
            raise RuntimeError('Primary key not found.')
        for k in mappings.keys():
            attrs.pop(k)
        # 将除主键外的其他属性变成`id`, `name`这种形式，关于反引号``的用法，可以参考点击打开链接
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))
        attrs['__mappings__'] = mappings # 保存属性和列的映射关系
        attrs['__table__'] = tableName # 保存表名
        attrs['__primary_key__'] = primaryKey # 主键属性名
        attrs['__fields__'] = fields # 除主键外的属性名
        # 构造默认的SELECT, INSERT, UPDATE和DELETE的SQL语句:
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)


# 首先要定义的是所有ORM映射的基类Model
# 基于字典查询形式
# Model从dict继承，拥有字典的所有功能，同时实现特殊方法__getattr__和__setattr__，能够实现属性操作
# 实现数据库操作的所有方法，定义为class方法，所有继承自Model都具有数据库操作方法
class Model(dict, metaclass=ModelMetaclass):

    def __init__(self, **kw):
        super(Model, self).__init__(**kw)

    # 通过这个方法得到实例User（dict）类的键值对，此方法为私有
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)

    # 通过这个方法设置实例User（dict）类的键值对
    def __setattr__(self, key, value):
        self[key] = value

    # 与上面同理，不过这是启动方法
    def getValue(self, key):
        return getattr(self, key, None)

    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key, str(value)))
                setattr(self, key, value)
        return value

    # 类方法有类变量cls传入，从而可以用cls做一些相关的处理。并且有子类继承时，调用该类方法时，传入的类变量cls是子类，而非父类。
    # 类方法的第一个参数应该是cls
    # 由哪一个类调用的方法，方法内的cls就是哪一个类的引用，这个参数和实例方法的第一个参数是self类似
    @classmethod
    async def findAll(cls, where=None, args=None, **kw):
        # find objects by where clause
        sql = [cls.__select__]
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []
        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)
        limit = kw.get('limit', None)
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):
                sql.append('?')
                args.append(limit)
            elif isinstance(limit, tuple) and len(limit) == 2 :
                sql.append('?, ?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s' % str(limit))
        rs = await select(' '.join(sql), args) # 返回的rs是一个元素是tuple的list，此处返回的size是默认值，即返回所有查询结果
        # 返回对象列表
        return [cls(**r) for r in rs] # **r 是关键字参数，构成了一个cls类的列表，其实就是每一条记录对应的类实例

    @classmethod
    async def findNumber(cls, selectField, where=None, args=None):
        # find number by select and where
        sql = ['select %s _num_ from `%s`' % (selectField, cls.__table__)]
        if where:
            sql.append('where')
            sql.append(where)
        # 搜寻数据库
        rs = await select(' '.join(sql), args, 1) # 此处返回的size是1，即返回1条查询结果
        if len(rs) == 0:
            return None
        return rs[0]['_num_']

    @classmethod
    async def find(cls, pk):
        # find object by primary key
        rs = await select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    # 往Model类添加实例方法，就可以让所有子类调用实例方法：
    async def save(self):
        args = list(map(self.getValueOrDefault, self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        # 执行数据库插入操作
        rows = await execute(self.__insert__, args)
        if rows != 1:
            logging.warn('failed to insert record: affected rows: %s' % rows)

    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warn('failed to update by primary key: affected rows: %s' % rows)

    async def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warn('failed to remove by primary key: affected rows: %s' % rows)

# 定义Field和各种Field子类（用来给元类做判断的，因为元类的attrs是子类的所有元素（包括__init__）所以要用一个类把参数筛选出来）
class Field(object):

    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)

class StringField(Field):

    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)

class BooleanField(Field):

    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)

class IntegerField(Field):

    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)

class FloatField(Field):

    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)

class TextField(Field):

    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)