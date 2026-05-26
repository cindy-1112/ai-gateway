import uvicorn
from app.config import load_config
from app.main import create_app


def main():
    config = load_config("config/gateway.yaml")
    app = create_app(config)
    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
