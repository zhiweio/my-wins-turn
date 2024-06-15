FROM python:3.9-slim

WORKDIR /app

RUN pip config set global.index-url https://pypi.mirrors.ustc.edu.cn/simple/

RUN pip install poetry

COPY pyproject.toml /app/

RUN poetry install --only main

RUN poetry cache clear --all pypi
RUN rm -rf /root/.cache/pip

COPY my_wins_turn/app.py /app/

ENTRYPOINT ["poetry", "run", "streamlit", "run", "app/app.py"]
