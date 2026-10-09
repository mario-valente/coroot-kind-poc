# Coroot + kind: POC local

**Resultado parcial:** fluxo e traces funcionando; latencia nas arestas do
Service Map nao disponivel neste ambiente kind. O sucesso de `verify.py` nao
significa que a captura eBPF L7 esteja completa.

Consulte [Descobertas e arquitetura](docs/descobertas.md) para os resultados,
limitacoes, integracoes com dados existentes e os modelos multi-cluster e
multi-tenant.

Fluxo real:

```text
load-generator -> app-a:8080/checkout -> app-b:8080/reserve -> redis:6379
                       \                       \
                        +----- OTLP/HTTP -------+--> Coroot -> ClickHouse

Coroot node-agent + cluster-agent -> Coroot -> Prometheus
```

## Acessar

- Coroot: <http://localhost:8080>
- Aplicacao: <http://localhost:8081/checkout>
- No projeto `default`, abra **Service Map** e localize as apps do namespace
  `demo`. Abra `app-a` e sua aba **Tracing**, com fonte **OpenTelemetry**.
- `python3 verify.py` imprime links diretos para o mapa e um trace novo.

O mapa usa conexoes observadas pelo agente, nao um desenho estatico.
Os traces sao enviados pelas apps via OpenTelemetry e armazenados no ClickHouse.
Esta POC usa um Prometheus novo, instalado pelo operador. Nao conecta nem altera
Prometheus, Tempo ou Loki existentes e nao instala Istio.

## Reproduzir

Requisitos: Linux com Docker, kind, kubectl, Helm, Python 3 e curl. A primeira
execucao precisa de internet para baixar charts, imagens e dependencias.

```bash
git clone https://github.com/mario-valente/coroot-kind-poc.git
cd coroot-kind-poc
bash up.sh
```

O cluster chama-se `coroot-poc`. Seu kubeconfig fica em `.kubeconfig`; os scripts
nao alteram o contexto Kubernetes padrao do usuario.
Charts, versoes dos agentes e Coroot estao fixados nos arquivos. As imagens base
Python e Redis usam tags de versao, nao digests imutaveis.

## Explorar os cenarios

```bash
curl -sS http://localhost:8081/checkout
curl -sS 'http://localhost:8081/checkout?scenario=slow'
curl -sS 'http://localhost:8081/checkout?scenario=error'
```

`normal` chama as duas apps e executa `INCRBY`, `SET` e `GET` no Redis.
`slow` acrescenta 600 ms no app-b antes do Redis.
`error` retorna 503 no app-b e 502 no app-a, intencionalmente, sem acessar Redis.
O atributo `business.feature=checkout` aparece nos spans HTTP.
O JSON de resposta inclui `trace_id`, igual nas duas apps.

O gerador faz aproximadamente uma requisicao por segundo, com pausas maiores no
cenario lento: 80% normais, 15% lentas e 5% erros simulados. Alertas ou erros nas
apps podem ser consequencia deliberada desses cenarios.

```bash
export KUBECONFIG="$HOME/coroot-kind-poc/.kubeconfig"
kubectl -n demo get pods
kubectl -n demo logs deployment/app-a --tail=20
kubectl -n demo logs deployment/load-generator --tail=20
kubectl -n demo exec deployment/redis -- redis-cli GET poc:checkout:reservations
```

## Verificacao automatizada

```bash
python3 verify.py
```

Verifica respostas, latencia simulada, erros, spans armazenados no Coroot,
relacoes pai/filho entre as apps e comandos Redis, e as tres arestas do Service
Map. Aguarda a ingestao e atualizacao do mapa; falha explicitamente se faltar
alguma aresta ou span.

## Limitacao importante do kind

A documentacao oficial do Coroot nao garante suporte a Kubernetes aninhado em
containers. Nesta maquina, o agente descobriu os workloads e as conexoes TCP
persistentes, mas nao forneceu as estatisticas L7 completas nas arestas.
Nao use esta POC para concluir que a cobertura eBPF equivale a um cluster real.

As apps reutilizam conexoes HTTP com keep-alive. O script reinicia o node-agent
depois de aquecer o trafego para que sua descoberta inicial encontre os sockets.
Se recriar as apps, repita a descoberta depois de restabelecer o trafego:

```bash
KUBECONFIG="$PWD/.kubeconfig" kubectl -n coroot rollout restart daemonset/coroot-node-agent
python3 verify.py
```

Tempos, erros e sequencia das chamadas sao demonstrados com os traces
OpenTelemetry, independentemente dessas limitacoes da captura eBPF.
Os logs de aplicacao incluem o trace_id; nao foi configurado um pipeline
OpenTelemetry de logs estruturados para garantir navegacao automatica log-trace.

## Escopo e seguranca

- As portas publicadas no host sao `127.0.0.1:8080` e `127.0.0.1:8081`.
- O Coroot permite acesso anonimo como Admin, apenas para este laboratorio.
  Nao exponha estas portas a outras maquinas nem reutilize isso em producao.
- Redis fica apenas dentro do cluster, sem autenticacao e sem persistencia.
- O node-agent e privilegiado e tem acesso aos recursos do node kind para eBPF.
- A chave de ingestao e aleatoria, armazenada em Secrets Kubernetes e nao no
  codigo. `.kubeconfig` tambem nao deve ser compartilhado ou versionado.
- Estatisticas de uso do Coroot estao desativadas. Nao foi configurado provedor
  externo de IA.
- Os containers e o trafego continuam enquanto Docker e o cluster estiverem
  ativos; nao dependem de um processo de port-forward nesta sessao.

## Pausar e remover

Pausar somente o trafego:

```bash
KUBECONFIG="$PWD/.kubeconfig" kubectl -n demo scale deployment/load-generator --replicas=0
```

Retomar: execute o mesmo comando com `--replicas=1`.

Remover **apenas** o cluster desta POC, inclusive os dados de observabilidade:

```bash
bash down.sh
```

Os arquivos da POC e as imagens no cache Docker permanecem.
