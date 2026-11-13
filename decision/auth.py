"""个人令牌校验。库内只比较哈希，日志不记录明文令牌。"""
import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)


class Unauthorized(PermissionError):
    """An active procurement supervisor must authorize the operation."""


def supervisor(connection, token):
    if not isinstance(token, str) or not token:
        logger.info('个人令牌缺失，已拒绝审批操作')
        raise Unauthorized('需要启用的采购主管个人令牌')
    supplied_hash = hashlib.sha256(token.encode()).hexdigest()
    with connection.cursor() as cursor:
        cursor.execute('SELECT id,name,role,enabled,token_hash FROM app_user')
        users = cursor.fetchall()
    actor = None
    for user in users:
        # 恒定时间比较，避免按令牌差异提前返回。
        if hmac.compare_digest(supplied_hash, user['token_hash']):
            actor = user
    if actor is None or not actor['enabled'] or actor['role'] != '采购主管':
        logger.info('个人令牌未对应启用的采购主管，已拒绝审批操作')
        raise Unauthorized('需要启用的采购主管个人令牌')
    logger.info('采购主管身份校验通过，用户编号 %s', actor['id'])
    return {'id': actor['id'], 'name': actor['name'], 'role': actor['role']}
