import multiprocessing

bind = "0.0.0.0:10000"
workers = 2
timeout = 120
preload_app = True

def on_starting(server):
    """gunicorn起動時にスケジューラを開始"""
    from scheduler import init_scheduler
    init_scheduler()
