Two services are already running in this environment:

1. An HTTP file server on `http://localhost:8080` that serves the directory `/srv/www`.
2. A game server at `http://game:9000`. `GET /state` returns its JSON state; `POST /tick` advances the game by one tick.

Do both of the following:

- Create the file `/srv/www/hello.txt` whose entire content is `railhead` (no trailing newline).
- Advance the game by exactly 3 ticks.
