# 第一步：编写一个协程函数：handle_url_xxx(request)
# 第二步，传入的参数需要自己从request中获取：url_param = request.match_info['key']，query_params = parse_qs(request.query_string)
# 最后，需要自己构造Response对象：text = render('template', data)，return web.Response(text.encode('utf-8'))
# 先得到URL处理函数 → 再定义RequestHander筛选参数顺便调用  → 再定义URL的注册函数

"""
问：为什么要通过RequestHandler类来创建url处理函数，前面不是已经有url处理函数了吗？为什么不直接拿来用？(灵魂拷问)
答：因为我们要通过HTTP协议来判断在GET或者POST方法中是否丢失了参数，如果判断方法编写在url处理函数中会有很多重复代码，因此用类来封装一下
（其实就是每次处理URL前要判断下参数，所以偷个懒写在一起了）
"""

import asyncio, os, inspect, logging, functools

from urllib import parse

from aiohttp import web

# apis是处理分页的模块,代码在本章页面末尾,请将apis.py放在www下以防报错
# APIError 是指API调用时发生逻辑错误
from apis import APIError


# 编写装饰函数@get(),在不改变核心属性的情况下增添附加功能：把一个函数映射为一个URL处理函数。这样，一个函数通过@get()的装饰就附带了URL信息（path & method）
def get(path):
    # Define decorator @get('/path')
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'GET'
        wrapper.__route__ = path
        return wrapper
    return decorator


# 编写装饰函数@post()
def post(path):
    # Define decorator @post('/path')
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'POST'
        wrapper.__route__ = path
        return wrapper
    return decorator


# URL处理函数不一定是一个协程，因此我们用RequestHandler()来封装一个URL处理函数
# 以下是RequestHandler需要定义的一些函数
def get_required_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
            args.append(name)
    return tuple(args)


def get_named_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            args.append(name)
    return tuple(args)


def has_named_kw_args(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            return True


def has_var_kw_arg(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True


def has_request_arg(fn):
    sig = inspect.signature(fn)
    params = sig.parameters
    found = False
    for name, param in params.items():
        if name == 'request':
            found = True
            continue
        if found and (param.kind != inspect.Parameter.VAR_POSITIONAL and param.kind != inspect.Parameter.KEYWORD_ONLY and param.kind != inspect.Parameter.VAR_KEYWORD):
            raise ValueError('request parameter must be the last named parameter in function: %s%s' % (fn.__name__, str(sig)))
    return found


# RequestHandler目的就是从URL函数中分析其需要接收的参数，从request中获取必要的参数，再调用URL函数，然后把结果转换为web.Response对象，这样，就完全符合aiohttp框架的要求
class RequestHandler(object):

    def __init__(self, app, fn):
        self._app = app
        self._func = fn
        self._has_request_arg = has_request_arg(fn)
        self._has_var_kw_arg = has_var_kw_arg(fn)
        self._has_named_kw_args = has_named_kw_args(fn)
        self._named_kw_args = get_named_kw_args(fn)
        self._required_kw_args = get_required_kw_args(fn)

    # 使得其实例变成可调用函数
    async def __call__(self, request):
        kw = None
        if self._has_var_kw_arg or self._has_named_kw_args or self._required_kw_args:
            if request.method == 'POST':
                # 判断content_type是否为空
                if not request.content_type:
                    return web.HTTPBadRequest(text='Missing Content-Type.')
                ct = request.content_type.lower()
                # startsWith()方法用来判断当前字符串是否是以另外一个给定的子字符串“开头”的
                if ct.startswith('application/json'):
                    # 请求json数据
                    params = await request.json()
                    if not isinstance(params, dict):
                        return web.HTTPBadRequest(text='JSON body must be object.')
                    kw = params
                elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
                    params = await request.post()
                    kw = dict(**params)
                else:
                    return web.HTTPBadRequest(text='Unsupported Content-Type: %s' % request.content_type)
            if request.method == 'GET':
                qs = request.query_string
                if qs:
                    kw = dict()
                    for k, v in parse.parse_qs(qs, True).items():
                        kw[k] = v[0]
        if kw is None:
            kw = dict(**request.match_info)
        else:
            if not self._has_var_kw_arg and self._named_kw_args:
                # 移除所有unnamed kw:
                copy = dict()
                for name in self._named_kw_args:
                    if name in kw:
                        copy[name] = kw[name]
                kw = copy
            # check named arg:
            for k, v in request.match_info.items():
                if k in kw:
                    logging.warning('Duplicate arg name in named arg and kw args: %s' % k)
                kw[k] = v
        if self._has_request_arg:
            kw['request'] = request
        # check required kw:
        if self._required_kw_args:
            for name in self._required_kw_args:
                if not name in kw:
                    return web.HTTPBadRequest(text='Missing argument: %s' % name)
        logging.info('call with args: %s' % str(kw))
        try:
            r = await self._func(**kw)
            return r
        except APIError as e:
            return dict(error=e.error, data=e.data, message=e.message)


# 定义add_static函数，来注册static文件夹下的文件
def add_static(app):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    # 官方文档方法
    app.router.add_static('/static/', path)
    logging.info('add static %s => %s' % ('/static/', path))


# 定义add_route函数，来注册一个URL处理函数（顺便协程化）
def add_route(app, fn):
    # 经过装饰器处理的URL会带上__method__，__route__属性
    method = getattr(fn, '__method__', None)
    path = getattr(fn, '__route__', None)
    if path is None or method is None:
        raise ValueError('@get or @post not defined in %s.' % str(fn))
    # 如果fn函数不是协程，则定义为协程
    # inspect.isgeneratorfunction(fn)检测fn是否为生成器方法（协程是由生成器实现的）
    if not asyncio.iscoroutinefunction(fn) and not inspect.isgeneratorfunction(fn):
        fn = asyncio.coroutine(fn)
    logging.info('add route %s %s => %s(%s)' % (method, path, fn.__name__, ', '.join(inspect.signature(fn).parameters.keys())))
    # RequestHandle类中有__callable__方法，因此RequestHandler(app, fn)相当于创建了一个url处理函数，函数名就是fn
    # 官方文档中的注册方法(请求request = 方法method + 路径path)
    app.router.add_route(method, path, RequestHandler(app, fn))


# 定义add_routes函数，自动把handler模块的所有符合条件的URL函数注册了
# add_routes只是用来批量注册的
def add_routes(app, module_name):
    '''
        返回'.'最后出现的位置
        如果为-1，说明是 module_name中不带'.',例如(只是举个例子) handles 、 models
        如果不为-1,说明 module_name中带'.',例如(只是举个例子) aiohttp.web 、 urlib.parse()    n分别为 7 和 5

        我们在app中调用的时候传入的module_name为handles,不含'.',if成立, 动态加载module
        比如 aaa.bbb 类型,我们需要从aaa中加载bbb
        n = 3
        name = module_name[n+1:] 为bbb
        module_name[:n] 为aaa
        mod = getattr(__import__(module_name[:n], globals(), locals(), [name]), name)，动态加载aaa.bbb
        上边三句其实相当于：
            aaa = __import__(module_name[:n], globals(), locals(), ['bbb'])
            mod = aaa.bbb
    '''
    n = module_name.rfind('.')
    if n == (-1):
        mod = __import__(module_name, globals(), locals())
    else:
        name = module_name[n+1:]
        mod = getattr(__import__(module_name[:n], globals(), locals(), [name]), name)
    # for循环把所有的url处理函数给得到了
    for attr in dir(mod):
        if attr.startswith('_'):
            continue
        # 给handlers模块中的URL处理函数命名，并传入
        fn = getattr(mod, attr)
        if callable(fn):
            method = getattr(fn, '__method__', None)
            path = getattr(fn, '__route__', None)
            # 注册url处理函数fn，如果不是url处理函数,那么其method或者route为none，自然也不会被注册
            if method and path:
                add_route(app, fn)