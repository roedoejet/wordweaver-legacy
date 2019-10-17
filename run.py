from wordweaver.app import app
from wordweaver.config import ENV_CONFIG

DEBUG = ENV_CONFIG['DEBUG']
HOST = ENV_CONFIG['HOST']
PORT = int(ENV_CONFIG['PORT'])
THREADED = ENV_CONFIG['THREADED']

app.run(debug=DEBUG, host=HOST, port=PORT, threaded=THREADED)