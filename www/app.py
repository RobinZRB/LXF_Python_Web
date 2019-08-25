import logging

logging.basicConfig(level=logging.INFO)

import asyncio, os, json, time
from datetime import datetime
from aiohttp import web
from jinja2 import Environment, FileSystemLoader

# config 配置代码在后面会创建添加, 可先从'https://github.com/yzyly1992/2019_Python_Web_Dev'下载或下一章中复制`config.py`和`config_default.py`到`www`下,以防报错
from config import configs
import orm
from coroweb import add_routes, add_static
# handlers 是url处理模块, 当handlers.py在API章节里完全编辑完再将下一行代码的双井号去掉
from handlers import cookie2user, COOKIE_NAME


# 初始化jinja2的函数（用于传送html模板）
def init_jinja2(app, **kw):
    logging.info('init jinja2...')
    options = dict(
        autoescape=kw.get('autoescape', True),
        block_start_string=kw.get('block_start_string', '{%'),
        block_end_string=kw.get('block_end_string', '%}'),
        variable_start_string=kw.get('variable_start_string', '{{'),
        variable_end_string=kw.get('variable_end_string', '}}'),
        auto_reload=kw.get('auto_reload', True)
    )
    path = kw.get('path', None)
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    logging.info('set jinja2 template path: %s' % path)
    # jinja2模块中有一个名为Environment的类，这个类的实例用于存储配置和全局对象，然后从文件系统或其他位置中加载模板
    # FileSystemLoader：文件系统加载器，不需要模板文件存在某个Python包下，可以直接访问系统中的文件（path为目录名称）
    env = Environment(loader=FileSystemLoader(path), **options)
    filters = kw.get('filters', None)
    if filters is not None:
        for name, f in filters.items():
            env.filters[name] = f
    app['__templating__'] = env


# middleware是一种拦截器，一个URL在被某个函数处理前，可以经过一系列的middleware的处理
# 以下是middleware,可以把通用的功能从每个URL处理函数中拿出来集中放到一个地方
# URL处理函数运行前进行一次拦截，响应生成前进行一次拦截
# URL处理日志工厂（拦截器）
async def logger_factory(app, handler):
    async def logger(request):
        # 打印URL的方法和路径
        logging.info('Request: %s %s' % (request.method, request.path))
        # 再执行URL处理
        return (await handler(request))

    return logger


# 认证处理工厂--把当前用户绑定到request上，并对URL/manage/进行拦截，检查当前用户是否是管理员身份
# 需要handlers.py的支持, 当handlers.py在API章节里完全编辑完再将下面代码的双井号去掉
async def auth_factory(app, handler):
    async def auth ( request ) :
        logging.info('check user: %s %s' % (request.method, request.path))
        request.__user__ = None
        cookie_str = request.cookies.get(COOKIE_NAME)
        if cookie_str :
            user = await cookie2user(cookie_str)
            if user :
                logging.info('set current user: %s' % user.email)
                request.__user__ = user
        if request.path.startswith('/manage/') and (request.__user__ is None or not request.__user__.admin) :
            return web.HTTPFound('/signin')
        return (await handler(request))

    return auth


# 数据处理工厂（拦截器）
# 这里的app就是里面的request
async def data_factory(app, handler):
    async def parse_data(request):
        if request.method == 'POST' :
            if request.content_type.startswith('application/json') :
                request.__data__ = await request.json()
                logging.info('request json: %s' % str(request.__data__))
            elif request.content_type.startswith('application/x-www-form-urlencoded') :
                request.__data__ = await request.post()
                logging.info('request form: %s' % str(request.__data__))
        return (await handler(request))

    return parse_data


# 响应返回处理工厂（拦截器）
# 接受的参数分别为请求实例和处理程序
async def response_factory(app, handler):
    async def response(request):
        logging.info('Response handler...')
        # 先得到URL处理后的数据，最终就可以得到response对象
        r = await handler(request)
        if isinstance(r, web.StreamResponse):
            return r
        if isinstance(r, bytes):
            resp = web.Response(body=r)
            resp.content_type = 'application/octet-stream'
            return resp
        if isinstance(r, str):
            if r.startswith('redirect:'):
                return web.HTTPFound(r[9:])
            resp = web.Response(body=r.encode('utf-8'))
            resp.content_type = 'text/html;charset=utf-8'
            return resp
        if isinstance(r, dict):
            template = r.get('__template__')
            if template is None:
                resp = web.Response(
                    body=json.dumps(r, ensure_ascii=False, default=lambda o: o.__dict__).encode('utf-8'))
                resp.content_type = 'application/json;charset=utf-8'
                return resp
            else:
                r['__user__'] = request.__user__
                resp = web.Response(body=app['__templating__'].get_template(template).render(**r).encode('utf-8'))
                resp.content_type = 'text/html;charset=utf-8'
                return resp
        if isinstance(r, int) and r >= 100 and r < 600:
            return web.Response(r)
        if isinstance(r, tuple) and len(r) == 2:
            t, m = r
            if isinstance(t, int) and t >= 100 and t < 600:
                return web.Response(t, str(m))
        # default:
        resp = web.Response(body=str(r).encode('utf-8'))
        resp.content_type = 'text/plain;charset=utf-8'
        return resp

    # 这是一个返回函数
    return response


# 时间转换
def datetime_filter(t):
    delta = int(time.time() - t)
    if delta < 60 :
        return u'1分钟前'
    if delta < 3600 :
        return u'%s分钟前' % (delta // 60)
    if delta < 86400 :
        return u'%s小时前' % (delta // 3600)
    if delta < 604800 :
        return u'%s天前' % (delta // 86400)
    dt = datetime.fromtimestamp(t)
    return u'%s年%s月%s日' % (dt.year, dt.month, dt.day)


# Web App骨架（上面的全是后期增添内容）
async def init(loop):
    await orm.create_pool(loop=loop, **configs.db)
    # 从aiohttp模块中调用WSGI接口方法，将客户端请求抛给web应用程序去处理，并启动拦截器
    # app是一个请求实例
    app = web.Application(loop=loop, middlewares=[
        logger_factory, response_factory, auth_factory
    ])
    # 注册模板
    init_jinja2(app, filters=dict(datetime=datetime_filter))
    # 注册url处理函数，注册后得到响应体response
    add_routes(app, 'handlers')
    # 注册静态文件
    add_static(app)
    srv = await loop.create_server(app.make_handler(), '127.0.0.1', 9000)
    logging.info('server started at http://127.0.0.1:9000...')
    return srv


loop = asyncio.get_event_loop()
loop.run_until_complete(init(loop))
loop.run_forever()
