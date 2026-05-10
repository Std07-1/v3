# Agent Memory Index — chart-ux-devops

## Feedback

- [stale-build-deployment-pattern](./feedback_stale_build_deployment.md)
  ws_server обслуговує старий bundle після npm run build якщо не перезапущений.
  Команда перевірки: `curl -s http://127.0.0.1:8000/ | grep -o 'assets/index[^"]*'`
  порівняти з `grep 'assets/index' ui_v4/dist/index.html`.
