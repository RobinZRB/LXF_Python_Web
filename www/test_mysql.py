import asyncio
import orm
import random
from models import User, Blog, Comment

async def test(loop):
    await orm.create_pool(loop, user='www-data', password='www-data', db='awesome')
    # 创建一个User类的实例（可以使用实例方法）
    u =User(name='test', email='test%s@example.com' % random.randint(0, 10000000), passwd='abc123456', image='about:blank')
    # 调用保存方法
    await u.save()


#要运行协程，需要使用事件循环
if __name__ == '__main__':
    # 创建事件循环
    loop = asyncio.get_event_loop()
    # 开始监听直到其完成
    loop.run_until_complete(test(loop))
    print('Test finished.')
    # 关闭事件循环（也可以forever）
    loop.close()