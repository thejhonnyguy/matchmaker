import json
import random
import time
from functools import wraps

from flask import (Blueprint, Flask, Response, redirect,
                   request, url_for)
from flask import render_template as _render_template
from flask_socketio import emit

from .db import HotSwapDB as LocalDB
from .. import socketio

app = Blueprint('draftv1', __name__, template_folder='templates',
                static_folder='static')
DB = LocalDB()


def render_template(*args, **kwargs):
    args = list(args)
    args[0] = 'draftv1_' + args[0]
    return _render_template(*args, **kwargs)


def assert_room_exists():
    def _assert_room_exists(fn):
        @wraps(fn)
        def _assert_room_exists_decorated(*args, **kwargs):
            room_id = kwargs.get('room_id')
            if not DB.room_exists(room_id):
                return redirect(url_for('.failure',
                                        reason='Room does not exist'))
            return fn(*args, **kwargs)

        return _assert_room_exists_decorated
    return _assert_room_exists


def benchmark_endpoint():
    def _benchmark_endpoint(fn):
        @wraps(fn)
        def _benchmark_endpoint_decorated(*args, **kwargs):
            rng = random.randint(100, 1000)
            start = time.time()
            print(f'start request {rng}')
            res = fn(*args, **kwargs)
            print(f'end request {rng} ({time.time() - start} seconds)')
            return res

        return _benchmark_endpoint_decorated
    return _benchmark_endpoint


def valid_secret():
    def _valid_secret(fn):
        @wraps(fn)
        def _valid_secret_decorated(*args, **kwargs):
            data = args[0]
            if not isinstance(data, dict):
                return
            if DB.secret_to_name(data.get('secret')) is None:
                return
            return fn(*args, **kwargs)

        return _valid_secret_decorated
    return _valid_secret


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/create')
def create_room():
    room_id = DB.create_room()
    return render_template('join_room.html', first=True,
                           room_id=room_id)


@app.route('/join/<room_id>')
@assert_room_exists()
def join_room(room_id):
    return render_template('join_room.html', first=False,
                           room_id=room_id)


@app.route('/waiting/<room_id>', methods=['POST'])
@assert_room_exists()
def join_room_go(room_id):
    name = request.form.get('name')
    auth = request.form.get('auth')
    if auth:
        name = DB.secret_to_name(auth)
        if name is None or name not in DB.get_room_info(room_id)['guests']:
            # user has been kicked
            return redirect(url_for('.failure', reason='You have been kicked'))
        success, message = True, auth
    elif not name:
        success, message = False, 'Please enter a name'
    else:
        success, message = DB.add_guest(room_id, name)
    if success:
        if name is None:
            name = DB.secret_to_name(message)
        return render_template('waiting_room.html', secret=message,
                               room_id=room_id, name=name)
    return redirect(url_for('.failure', reason=message))


@app.route('/waiting/<room_id>')
@assert_room_exists()
def bad_join_room(room_id):
    return redirect(url_for('.join_room', room_id=room_id))


@app.route('/drafting/<room_id>', methods=['POST'])
@assert_room_exists()
def drafting_page(room_id):
    auth = request.form.get('auth')
    name = DB.secret_to_name(auth)
    if auth is None or name is None:
        return redirect(url_for('.failure',
                                reason='Authentication failure'))
    room_info = DB.get_room_info(room_id)
    if (sum(data['captain'] for _, data in room_info['guests'].items()) != 3
            or len(room_info['order']) != 10):
        return redirect(url_for('.failure', reason='Room not configured '
                                'to draft'))
    captain_status = room_info['guests'][name]['captain']
    return render_template('draft.html', secret=auth, room_id=room_id,
                           captain_status=captain_status)


@app.route('/results/<room_id>', methods=['POST'])
@assert_room_exists()
def results_page(room_id):
    auth = request.form.get('auth')
    name = DB.secret_to_name(auth)
    if auth is None or name is None:
        return redirect(url_for('.failure',
                                reason='Authentication failure'))
    room_info = DB.get_room_info(room_id)
    team = (name in room_info['teams'][1]) + 1
    return render_template('results.html', name=name, team=team, secret=auth,
                           teams=room_info['teams'])


@app.route('/uhoh/<reason>')
def failure(reason):
    return render_template('errorpage.html', reason=reason)


@socketio.on('getinfo')
def socket_getinfo(data):
    if not isinstance(data, dict):
        return
    secret = data.get('secret')
    room_id = DB.secret_to_room_id(secret)
    if room_id is None:
        emit('infoupdate', {'kicked': True})
        return
    req_from = DB.secret_to_name(secret)
    room_info = DB.get_room_info(room_id)
    # is the requesting player owner?
    owner = False
    if room_info['guests'][req_from]['owner']:
        owner = True
    captains = [room_info['guests'][guest]['captain']
                for guest in room_info['order']]
    emit('infoupdate', {'players': room_info['order'], 'owner': owner,
                        'captains': captains, 'stage': room_info['stage']})


@socketio.on('requestkick')
@valid_secret()
def socket_requestkick(data):
    secret = data.get('secret')
    kick = data.get('kick')
    room_id = DB.secret_to_room_id(secret)
    req_from = DB.secret_to_name(secret)
    room_info = DB.get_room_info(room_id)
    if not room_info['guests'][req_from]['owner']:
        return
    if kick not in room_info['guests']:
        return
    kick_secret = room_info['guests'][kick]['secret']
    DB.kick_user(kick_secret)
    emit('infoupdate', {'players': DB.get_room_info(room_id)['order']})


@socketio.on('requestcaptain')
@valid_secret()
def socket_requestcaptain(data):
    secret = data.get('secret')
    captain = data.get('captain')
    room_id = DB.secret_to_room_id(secret)
    req_from = DB.secret_to_name(secret)
    room_info = DB.get_room_info(room_id)
    if not room_info['guests'][req_from]['owner']:
        return
    if captain not in room_info['guests']:
        return
    captain_secret = room_info['guests'][captain]['secret']
    DB.set_captain(captain_secret)
    room_info = DB.get_room_info(room_id)
    update = [room_info['guests'][guest]['captain']
              for guest in room_info['order']]
    emit('infoupdate', {'captains': update})


@socketio.on('requestdraft')
@valid_secret()
def socket_requestdraft(data):
    secret = data.get('secret')
    room_id = DB.secret_to_room_id(secret)
    req_from = DB.secret_to_name(secret)
    room_info = DB.get_room_info(room_id)
    if not room_info['guests'][req_from]['owner']:
        return
    if room_info['stage']:
        return
    DB.advance_stage(room_id)


@socketio.on('draftinfo')
@valid_secret()
def socket_draftinfo(data):
    secret = data.get('secret')
    room_id = DB.secret_to_room_id(secret)
    req_from = DB.secret_to_name(secret)
    room_info = DB.get_room_info(room_id)
    result = {'teams': room_info['teams'],
              'drafting': room_info['draft_order'
                                    ][min(room_info['stage'] - 1, 7)],
              'stage': room_info['stage']}
    if req_from not in room_info['captains']:
        emit('draftupdate', result)
        return
    captain_n = room_info['captains'].index(req_from)
    coins = [room_info['guests'][req_from]['coins'],
             room_info['guests'][room_info['captains'][1 - captain_n]]['coins']
             ]
    result['coins'] = coins
    result['intent'] = room_info['intents'
                                 ][min(room_info['stage'] - 1, 7)][captain_n]
    emit('draftupdate', result)


@socketio.on('draftbid')
@valid_secret()
def socket_draftinfo(data):
    secret = data.get('secret')
    try:
        payment = max(int(data.get('payment')), 0)
    except ValueError:
        return
    cli_stage = data.get('stage')
    accept = data.get('accept')
    if payment is None or cli_stage is None or accept is None:
        return
    room_id = DB.secret_to_room_id(secret)
    req_from = DB.secret_to_name(secret)
    room_info = DB.get_room_info(room_id)
    if req_from not in room_info['captains']:
        return
    if room_info['stage'] != cli_stage or not 0 < room_info['stage'] < 9:
        return
    if room_info['guests'][req_from]['coins'] < payment:
        return
    complete = DB.set_payment(room_id, payment, accept,
                              room_info['guests'][req_from]['captain'] - 1)
    emit('draftupdate', {'intent': [payment, accept], 'accept': accept})
