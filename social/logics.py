import datetime

from django.core.cache import cache

from libs.cache import rds
from swiper import config
from common import errors
from common import keys
from user.models import User
from social.models import Swiped
from social.models import Friend


def get_rcmd_list(user, limit):
    '''获取推荐列表'''
    # 计算出出生年的范围
    curr_year = datetime.date.today().year  # 当前年份
    max_birth_year = curr_year - user.profile.min_dating_age
    min_birth_year = curr_year - user.profile.max_dating_age

    # 取出需要排出的用户 ID 列表
    sid_list = Swiped.objects.filter(uid=user.id).values_list('sid', flat=True)

    # 执行过滤
    rcmd_users = User.objects.filter(
        sex=user.profile.dating_sex,
        location=user.profile.location,
        birth_year__gt=min_birth_year,
        birth_year__lt=max_birth_year,
    ).exclude(id__in=sid_list)[:limit]

    return rcmd_users


def like_someone(user, sid):
    # 添加滑动记录
    Swiped.swipe(user.id, sid, 'like')

    # 检查对方是否喜欢过自身, 如果喜欢过，建立好友关系
    if Swiped.is_liked(sid, user.id):
        Friend.make_friends(user.id, sid)
        return True
    else:
        return False


def superlike_someone(user, sid):
    # 添加滑动记录
    Swiped.swipe(user.id, sid, 'superlike')

    # 检查对方是否喜欢过自身, 如果喜欢过，建立好友关系
    if Swiped.is_liked(sid, user.id):
        Friend.make_friends(user.id, sid)
        return True
    else:
        return False


def rewind(user):
    '''反悔操作'''
    key = keys.REWIND_TIMES % user.id
    # 检查当天反悔操作是否已达上限
    rewind_times = cache.get(key, 0)
    if rewind_times >= config.REWIND_LIMIT:
        raise errors.RewindLimited

    # 找出最后一次滑动记录
    latest_swiped = Swiped.objects.filter(uid=user.id).latest('stime')

    # 检查之前是否成功匹配为好友，如果是好友则断交
    if latest_swiped.stype in ['like', 'superlike']:
        Friend.break_off(user.id, latest_swiped.sid)  # 有则删除，没有则什么也不做

    # 恢复滑动积分
    score = config.SWIPE_SCORE[latest_swiped.stype]
    rds.zincrby(keys.SWIPE_RANK, -score, latest_swiped.sid)

    # 删除滑动记录
    latest_swiped.delete()

    # 重设缓存
    rewind_times += 1
    now_time = datetime.datetime.now().time()
    remain_time = 86400 - now_time.hour * 3600 - now_time.minute * 60 - now_time.second
    cache.set(key, rewind_times, remain_time)

    # 另一种计算今天剩余秒数的方法
    # remain_time = 86400 - (time.time() + 3600 * 8) % 86400


def add_swipe_score(swipe_view_func):
    def wrapper(request):
        response = swipe_view_func(request)

        if 200 <= response.status_code < 300:
            # 记录被滑动用户的积分
            stype = swipe_view_func.__name__
            score = config.SWIPE_SCORE[stype]
            sid = request.POST.get('sid')
            rds.zincrby(keys.SWIPE_RANK, score, sid)

        return response
    return wrapper


def get_top_n(num):
    '''获取排行前 N 的用户数据'''
    # 数据格式
    # origin_data = [
    #     (b'575', 920.0),  # 第一项是 uid, 第二项是"用户积分"
    #     (b'778', 624.0),
    #     (b'632', 520.0),
    # ]
    origin_data = rds.zrevrange(keys.SWIPE_RANK, 0, num - 1, withscores=True)

    # 整理数据格式
    cleaned_data = [[int(uid), int(score)] for uid, score in origin_data]

    # 生成排名数据
    rank_data = {}
    for rank, (uid, score) in enumerate(cleaned_data, 1):
        user = User.get(id=uid)  # NOTE: 此处可以改成批量获取，提升数据操作上的性能
        user_data = user.to_dict()
        user_data['score'] = score
        rank_data[rank] = user_data

    return rank_data
