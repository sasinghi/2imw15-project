import tweepy
import time
import csv
import pickle
import re
import os

################################################
# INSTANTIATE API
################################################


def switch_auth(idx):
    """ Switch current api authentication """
    assert isinstance(idx, int)
    if idx >= len(api.auths):
        raise IndexError('Index out of bounds.')
    api.auth_idx = idx
    api.auth = api.auths[idx]


def handle_rate_limit(resource, path):
    """ Switch authentication from the current one which is depleted """
    assert isinstance(resource, str) and isinstance(path, str)
    print('\t--> Handling Rate Limit')

    # Get rate limit status of all OAuth credentials
    _rate_limit_status = []
    for auth in api.auths:
        api.auth = auth
        result = api.rate_limit_status()['resources'][resource][path]
        _rate_limit_status.append(result)

    # IF maximum remaining calls in all auths is 0
    # THEN sleep till reset time.
    idx = max(enumerate(_rate_limit_status), key=lambda x: x[1]['remaining'])[0]
    if _rate_limit_status[idx]['remaining'] == 0:
        # Pick auth with minimum reset time
        idx = min(enumerate(_rate_limit_status), key=lambda x: x[1]['reset'])[0]
        sleep_time = _rate_limit_status[idx]['reset'] - int(time.time())
        if sleep_time > 0:
            print('\t--> Going to sleep now!')
            time.sleep(sleep_time + 5)
            print("\t--> Good morning")

    # Pick auth with maximum remaining calls
    switch_auth(idx)


def remaining_calls(resource, path):
    """ Get the remaining number of calls left for a given API resource """
    assert isinstance(resource, str) and isinstance(path, str)
    result = api.rate_limit_status()['resources'][resource][path]['remaining']
    print('Remaining calls for', path, ':', result)
    return result


def load_auth_handlers_from_file(filename):
    """
    Load all the OAuth handlers
    :return: list of OAuth handlers
    """
    auths = []
    credentials_file = open(filename, 'r')
    credentials_reader = csv.DictReader(credentials_file)
    for cred in credentials_reader:
        auth = tweepy.OAuthHandler(cred['consumer_key'], cred['consumer_secret'])
        auth.set_access_token(cred['access_token'], cred['access_secret'])
        auths.append(auth)
    if not auths:
        raise ValueError('No OAuth handlers available.')
    print('Imported %s twitter credentials' % len(auths))
    return auths

# Load the Twitter API
auths = load_auth_handlers_from_file('twitter_credentials.csv')
api = tweepy.API(auths[0], retry_count=3, retry_delay=5,
                 retry_errors={401, 404, 500, 503})
api.auths = list(auths)
api.auth_idx = 0
auths = None

################################################
# COLLECT TWEETS
################################################


def set_users(list_of_users):
    """ Add give users to pickle file """
    assert isinstance(list_of_users, list) and (all(isinstance(elem, str) for elem in list_of_users))
    users = list_of_users
    pickle.dump(users, open("users.p", "wb"))


def get_users():
    """ Get list of users """
    users = pickle.load(open("users.p", "rb"))
    return users


def cursor_iterator(cursor, resource, path):
    """ Iterator for tweepy cursors """
    # First check to make sure enough calls are available
    if remaining_calls(resource, path) == 0:
        handle_rate_limit(resource, path)
    err_count = 0

    while True:
        try:
            yield cursor.next()
            remaining = int(api.last_response.headers['x-rate-limit-remaining'])
            if remaining == 0:
                handle_rate_limit(resource, path)
        except tweepy.RateLimitError as e:
            print(e.reason)
            err_count += 1
            if err_count > 1:
                break
            else:
                handle_rate_limit(resource, path)
        except tweepy.error.TweepError as e:
            print(e.response)
            print(e.api_code)
            err_count += 1
            if err_count > 1:
                break
            elif e.api_code == 429:
                # elif isinstance(e.message, list) and len(e.message) > 0 \
                #         and 'code' in e.message[0] \
                #         and e.message[0]['code'] == 429:
                handle_rate_limit(resource, path)
        except Exception as e:
            print(e)
            break
        else:
            err_count = 0


def check_keyword(s, key):
    """ Check if keyword exists in string """
    return bool(re.search(key, s, re.IGNORECASE))


def get_tweets_of_user(screen_name, nr_of_tweets=-1, keywords=set(), save_to_csv=True):
    """ Get all (max 3240 recent) tweets of given screen name """
    assert isinstance(screen_name, str)
    assert isinstance(keywords, set) and all(isinstance(k, str) for k in keywords)
    assert isinstance(nr_of_tweets, int) and nr_of_tweets >= -1
    assert isinstance(save_to_csv, bool)

    # Resource from which we want to collect tweets
    resource, path = 'statuses', '/statuses/user_timeline'

    # initialize a list to hold all the tweets
    alltweets = []

    try:
        for page in cursor_iterator(
                tweepy.Cursor(api.user_timeline, screen_name=screen_name,
                              count=200, include_rts=True).pages(), resource, path):
            alltweets.extend(page)
            print("...%s tweets downloaded so far" % len(alltweets))
            if 0 < nr_of_tweets <= len(alltweets):
                break
    except KeyboardInterrupt:
        pass

    # transform the tweepy tweets into a 2D array that will populate the csv
    outtweets = [[tweet.id_str,
                  tweet.text.replace('\n', ' ').replace('\r', ''),
                  tweet.created_at,
                  tweet.retweet_count,
                  1 if tweet.in_reply_to_user_id is not None else 0,
                  tweet.in_reply_to_user_id if tweet.in_reply_to_user_id is not None else -1,
                  tweet.in_reply_to_status_id_str if tweet.in_reply_to_status_id_str is not None else -1,
                  tweet.author.id,
                  tweet.author.name,
                  tweet.author.created_at,
                  tweet.author.followers_count,
                  tweet.author.friends_count,
                  tweet.author.statuses_count,
                  tweet.author.listed_count,
                  tweet.author.favourites_count,
                  1 if tweet.author.verified else 0,
                  [k for k in keywords if check_keyword(tweet.text, k)],
                  [hashtag['text'] for hashtag in tweet.entities['hashtags']],
                  [url['expanded_url'] for url in tweet.entities['urls']]] for tweet in alltweets]

    features = ["tweet_id", "text", "created_at", "retweet_count", "is_reply", "reply_to_user_id",
                "reply_to_tweet_id", "user_id", "screen_name", "user_created_at", "#followers",
                "#followings", "#statuses", '#listed', "#favourites", "verified", "keywords",
                "hashtags", "urls"]

    if save_to_csv:
        with open(os.path.join('results', '%s_tweets.csv' % screen_name), 'w', newline='', encoding='utf8') as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(features)
            writer.writerows(outtweets)

    return features, outtweets


def get_all_tweets_of_users(list_of_users, nr_of_tweets=-1, keywords=set()):
    """ Get the tweets all given users in list """
    assert isinstance(list_of_users, list) and all(isinstance(elem, str) for elem in list_of_users)
    for user in list_of_users:
        print('Getting tweets for %s' % user)
        get_tweets_of_user(user, nr_of_tweets=nr_of_tweets, keywords=keywords)


def get_friends_of_user(screen_name):
    """ Get all friends of the given user
    :param screen_name: Twitter screen name of the given user
    :return: List of all friends of given user
    """
    assert isinstance(screen_name, str)

    # Resource from which we want to collect tweets
    resource, path = 'friends', '/friends/list'

    # initialize a list to hold all the friends screen names
    users = []

    for page in cursor_iterator(
            tweepy.Cursor(api.friends, screen_name=screen_name, count=200).pages(), resource, path):
        users.extend(page)
        print('...%s friends found so far' % len(users))

    # transform the tweepy friends into a 2D array that will populate the csv
    outfriends = [[screen_name,
                   user.id_str,
                   user.screen_name,
                   user.followers_count,
                   user.friends_count,
                   user.listed_count,
                   user.statuses_count] for user in users]

    features = ["user_screen_name", "friend_id", "friend_screen_name", "friends_#followers",
                "friends_#followings", "friends_#listed", "friends_#statuses"]

    with open(os.path.join('results', '%s_friends.csv' % screen_name), 'w', newline='', encoding='utf8') as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(features)
        writer.writerows(outfriends)

    return features, outfriends


def get_friends_of_users(list_of_users):
    """ Get the tweets all given users in list """
    assert isinstance(list_of_users, list) and all(isinstance(elem, str) for elem in list_of_users)
    for user in list_of_users:
        print('Getting friends of %s' % user)
        get_friends_of_user(user)


def get_user_info(screen_name):
    """
    Get user object for given screen_name
    :param screen_name: the user
    :return: User object
    """
    assert isinstance(screen_name, str)
    return api.get_user(screen_name=screen_name)


def check_query(s):
    """ Checks for common search API query keywords """
    return (check_keyword(s, 'from:')
            or check_keyword(s, 'to:')
            or check_keyword(s, 'list:')
            or check_keyword(s, 'filter:')
            or check_keyword(s, 'url:')
            or check_keyword(s, 'since:')
            or check_keyword(s, 'until:')
            or s == 'OR' or s == '"'
            or s == '#' or s == '?'
            or s == ':)' or s == ':('
            or s[0] == '-' or s[0] == '@' or s[0] == '#')


def search_tweets(qry, nr_of_tweets=-1, since_id=None, max_id=None, save_to_csv=True):
    assert isinstance(qry, str)
    assert isinstance(max_id, int) or max_id is None
    assert isinstance(since_id, int) or since_id is None
    assert isinstance(nr_of_tweets, int) and nr_of_tweets >= -1
    assert isinstance(save_to_csv, bool)

    # Get all the relevant keywords from the query
    import shlex
    keywords = set(s.replace('(', '').replace(')', '') for s in shlex.split(qry) if not check_query(s))
    print('keywords: ', keywords)

    from datetime import datetime
    time = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Resource from which we want to collect tweets
    resource, path = 'search', '/search/tweets'

    # initialize a list to hold all the tweets
    alltweets = []

    try:
        for page in cursor_iterator(
                tweepy.Cursor(api.search, q=qry, count=200, lang='en', since_id=since_id,
                              max_id=max_id).pages(), resource, path):
            alltweets.extend(page)
            print("...%s tweets downloaded so far" % len(alltweets))
            if 0 < nr_of_tweets <= len(alltweets):
                break
    except KeyboardInterrupt:
        pass

    # transform the tweepy tweets into a 2D array that will populate the csv
    outtweets = [[tweet.id_str,
                  tweet.text.replace('\n', ' ').replace('\r', ''),
                  tweet.created_at,
                  tweet.retweet_count,
                  1 if tweet.in_reply_to_user_id is not None else 0,
                  tweet.in_reply_to_user_id if tweet.in_reply_to_user_id is not None else -1,
                  tweet.in_reply_to_status_id_str if tweet.in_reply_to_status_id_str is not None else -1,
                  tweet.author.id,
                  tweet.author.name,
                  tweet.author.created_at,
                  tweet.author.followers_count,
                  tweet.author.friends_count,
                  tweet.author.statuses_count,
                  tweet.author.listed_count,
                  tweet.author.favourites_count,
                  1 if tweet.author.verified else 0,
                  [k for k in keywords if check_keyword(tweet.text, k)],
                  [hashtag['text'] for hashtag in tweet.entities['hashtags']],
                  [url['expanded_url'] for url in tweet.entities['urls']]] for tweet in alltweets]

    features = ["tweet_id", "text", "created_at", "retweet_count", "is_reply", "reply_to_user_id",
                "reply_to_tweet_id", "user_id", "screen_name", "user_created_at", "#followers",
                "#followings", "#statuses", '#listed', "#favourites", "verified", "keywords",
                "hashtags", "urls"]

    if save_to_csv:
        with open(os.path.join('results', 'search_%s_tweets.csv' % time),
                  mode='w', newline='', encoding='utf8') as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(['#query', qry])
            writer.writerow(features)
            writer.writerows(outtweets)

    return features, outtweets

if __name__ == "__main__":
    # Set users from whom to get tweets
    # set_users(list_of_users=['vote_leave', 'BorisJohnson', 'David_Cameron',
    #                          'Nigel_Farage', 'michaelgove', 'George_Osborne'])

    # Load users
    # users = get_users()

    # Get tweets
    # get_all_tweets_of_users(users, keywords=["people", "twitter"])

    # Get friends
    # get_friends_of_users(users)

    # Remaining calls
    # resource, path = 'statuses', '/statuses/user_timeline'
    # remaining_calls(resource, path)

    # Search tweets on keywords
    # since_id = most recent tweet id
    # max_id = oldest retrieved tweet id - 1
    query = '(britain eu) ' \
            'OR ((uk OR britain OR ukip) referendum) ' \
            'OR brexit ' \
            'OR #voteleave ' \
            'OR #votestay ' \
            'OR #EUreferendum ' \
            'OR #StrongerIn ' \
            'OR #Euref ' \
            'OR #Remain ' \
            'OR #voteremain'
    # search_tweets(query, nr_of_tweets=20000, since_id=790324301446676480)#, max_id=790314732670640127)
