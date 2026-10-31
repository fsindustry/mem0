"""入口:python -m ksc_mem0_server"""

import uvicorn

from .app import create_app
from .config import get_settings

app = create_app()

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(app, host=s.host, port=s.port)
