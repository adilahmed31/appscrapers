from scraper.db_util import (
    db_connect, upsert, app_table_name, exists, _id_column_type, get_all_terms, get_all_terms_LANG_COUNTRY, reviews_table_name,
)
from scraper.appstore_api import get_store_func
import scraper.config as config
import json
import sys
from datetime import datetime
import threading

logger = config.setup_logger()
SERIALIZED_KEYS = [
    'similar', 'permissions', 'reviews', 'screenshots', 'comments',
    'recentChanges', 'histogram',
    # ios only variables
    'appletvScreenshots', 'genres', 'languages', 'genreIds',
    'ipadScreenshots', 'supportedDevices'
]

def get_similar_apps(appid, store, limit=50):
    similar = get_store_func('similar', store)
    return [
        a['appId'] for a in
        similar({'appId': appid, 'lang':config.LANG, 'country':config.COUNTRY, 'fullDetail': False, 
                 'throttle': config.THROTTLE_DEFAULT})
    ][:limit]


def get_permissions(appid, store):
    if store != 'android': return ['Not Available']
    permissions = get_store_func('permissions', store)
    return permissions({'appId': appid, 'short': True})


def get_app_details(appid, store):
    """
    Get app details from the database. Returns None in case not found. 
    Returns all the details of the appid stored in the db sorted by their download date.
    """
    db = db_connect()
    table = db.get_table(
        # app_table_name(store), primary_id='appId', primary_type=_id_column_type()
        app_table_name(store)
    )
    already_exists = exists(table, 'appId', appid, time_check=force)    
    if not already_exists:
        return []
    
    ret = list(db.query(
        'select * from {0} where appId="{1}" COLLATE NOCASE order by time'
            .format(table.table.name, appid)
    ))[0]
    for k in SERIALIZED_KEYS:
        if k in ret:
            try:
                ret[k] = json.loads(ret[k])
            except Exception as e:
                logger.debug("get_app_details ({}) from {}".format(appid, store))
                logger.exception(e)
                ret[k] = []
    return ret


def download_app_details(appid, store, force=False):
    """Downloads details of an app (@appid) from the corresponding store (@store),
    and saves it as necessary. 
    """

    db = db_connect()
    table = db.get_table(
        # app_table_name(store), primary_id='appId', primary_type=_id_column_type()
        app_table_name(store)
    )

    already_exists = exists(table, 'appId', appid, time_check=force)

    appfunc = get_store_func('app', store)
    if store == 'ios' and (appid.startswith('id') or appid.isdigit()):
        already_exists = exists(table, 'iosid', appid, time_check=force)
        if already_exists:
            appid = table.find_one(iosid=appid)['appId']
        ret = appfunc({'id': appid.replace('id', ''),
                       'throttle': config.THROTTLE_DEFAULT})
        if ret: 
            ret['iosid'] = ret['id']
            del ret['id']
            appid = ret['appId']
    else:
        ret = appfunc({'appId': appid, 'lang':config.LANG, 'country':config.COUNTRY, 'throttle': config.THROTTLE_DEFAULT})

    if ret and not ret['appId']:   # WTF is going on
        logger.warning("WTF: appId={}, store={}".format(appid, store))
        return None

    if not ret:
        if already_exists:
            # q = "update {table} set discontinued={time!r} where "\
            #     "appId={appid!r} and discontinued is null".format(
            #         table=table.table.name,
            #         time=config.now(),
            #         appid=appid
            #     )
            #db.query(q)
            data = dict(appId=appid,discontinued=config.now())
            table.update(data,['appId'])
        logger.info("No app with appId={}".format(appid))
        return None
    # get permissions and similar apps Similar apps was supposed to be
    # closure like terms, but, similar apps diverge very fast, and
    # the closure set grows too much. So right now it is capped to 20
    # similar apps
    # ret['similar'] = [ x for x in get_closure_of_apps([appid],
    #     store=store, limit=config.APPS_PER_QUERY) ]
    ret['similar'] = get_similar_apps(appid, store=store)
    current_time = datetime.now().timestamp()
    #Bugfix: if store is ios, datetime is returned as a string (e.g:2021-08-22T05:05:30Z) 
    # but android returns a timestamp (e.g: 1628091891000.0). Modified code to handle both conditions.
    if store == 'ios':
        updated_time = datetime.strptime(ret['updated'],'%Y-%m-%dT%H:%M:%SZ')
        updated_time = float(datetime.timestamp(updated_time))
    else:
        updated_time = ret['updated']
    # If the app is in the playstore updated long time ago (more than a month),
    # then we already have the most updated version.
    if already_exists and \
       ((current_time - updated_time) > 30 * 86400):
        return None
    if store == 'android':
        # ret['permissions'] = [x for x in permissions({'appId': appid,
        # 'short': True})]
        ret['permissions'] = get_permissions(appid, store)
        ret['LANG'] = config.LANG
        ret['COUNTRY'] = config.COUNTRY
    else:
        ret['permissions'] = ['<not available>']
    ret['time'] = config.now()
    ret['lastseen'] = ret['time']
    ret['discontinued'] = None
    ret['LANG'] = config.LANG
    ret['COUNTRY'] = config.COUNTRY
    serialize_keys = ['terms', 'apps']
    for k, v in ret.items():
        if isinstance(v, (list, set, dict)):
            ret[k] = json.dumps(v)

    for k in ret:
        if isinstance(ret[k], (list, set)):
            logger.warning("get_app_details.1 >> ", store, k)
    upsert(table, ret, ['appId', 'description', 'title',
                        'permissions', 'updated'])

    # q = "update {table} set lastseen={time!r} where "\
    #     "appId={appid!r}".format(
    #         table=table.table.name,
    #         time=config.now(),
    #         appid=ret['appId']
    #     )
    #Refactored code to avoid errors with return values
    data = dict(appId=ret['appId'],lastseen=config.now())
    table.update(data,['appId'])
    #db.query(q)


def download_reviews(appid, store, limit=100):
    db = db_connect()
    # Id provided for each app
    table = db.get_table(reviews_table_name(store), primary_id='id',
                         primary_type=_id_column_type())
    db.commit()
    # table.create_index(['appId'])
    reviews_func = get_store_func('reviews', store)
    page = 0
    have_seen_this_app = exists(table, 'appId', appid)
    rev_tot = 2 ** 32 - 1
    try:
        rev_count = int(db.query(
            'select count(*) c from {} where appId={!r} COLLATE NOCASE'
                .format(table.table.name, appid)
        ).next()['c'])
    except Exception as e:
        print(e, store, e, appid, file=sys.stderr)
        rev_count = 0

    try:
        rev_tot = int(db.query(
            'select reviews from {} where appId={!r} COLLATE NOCASE'
                .format(app_table_name(store), appid)
        ).next()['reviews'])
    except Exception as e:
        print("download_reviews.1>>", store, e, appid, file=sys.stderr)
        rev_tot = rev_count * 2

    ids_already_there = set()
    comments = []
    if rev_count > 0:
        comments = list(table.find(appId=appid))
        ids_already_there = set(
            str(r['id']) for r in
            db.query(
                'select id from {tabname} where appId="{appid}" COLLATE NOCASE'
                    .format(tabname=table.table.name, appid=appid)
            )
        )

    def add_appid(d):
        d['appId'] = appid
        return d

    while len(comments) < min(limit, rev_tot):
        # Google is real angry, setting the throttle to 2 req per sec.
        ret = reviews_func({'appId': appid, 'page': page, 'lang':config.LANG,
                            'throttle': config.THROTTLE_DEFAULT-3}) 
        if not ret: break
        #Bugfix: Handling a difference in format of the JSON objects returned from Play Store and App Store scrapers
        if store == 'android':
            ret = ret['data']
            #Bugfix: Hack to solve an issue
            # where the criterias parameter returns a blank list [] and causes an error when inserted into the DB
            for r in ret:
                r["criterias"] = None
        ret = [add_appid(r) for r in ret if r['id'] not in ids_already_there]
        assert len(ret) == len(set(r['id'] for r in ret)), ret
        rev_count += len(ret)
        page += 1
        try:
            table.insert_many(ret)
        except Exception as e:
            logger.exception(e)
            logger.info(json.dumps(ret, indent=4))
            logger.info(ids_already_there)
            print("Could not insert. Exiting.... See appscraper.log")
            exit(-1)
        ids_already_there |= set(r['id'] for r in ret)
        comments.extend(ret)
        #The program does not terminate when reviews are fetched from the android app store. 
        # This is a temporary hack till I can figure out what process is causing the issue.
        #TODO: Debug and fix script not terminating issue. 
        if store == 'android':
            sys.exit()
    db.commit()
    return comments
