import config_default


class Dict(dict):
    # 重写属性设置，获取方法
    # 支持通过属性名访问键值对的值，属性名将被当做键名
    # dict转换成Dict后可以这么读取配置：configs.db.host就能读到host的值。 当然configs[db][host]也可以读到
    def __init__(self, names=(), values=(), **kw):
        super(Dict, self).__init__(**kw)
        for k, v in zip(names, values):
            self[k] = v

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Dict' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key] = value


# 以递归的手法将override中的host覆盖default中的host
def merge(defaults, override):
    r = {}
    for k, v in defaults.items():
        if k in override:
            if isinstance(v, dict):
                r[k] = merge(v, override[k])
            else:
                r[k] = override[k]
        else:
            r[k] = v
    return r

# 将传入参数d中的子可迭代对象转换成自定义的Dict形式
def toDict(d):
    D = Dict()
    for k, v in d.items():
        D[k] = toDict(v) if isinstance(v, dict) else v
    return D

configs = config_default.configs

try:
    import config_override
    # 合并
    configs = merge(configs, config_override.configs)
except ImportError:
    pass

configs = toDict(configs)