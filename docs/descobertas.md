# Descobertas da POC: arquitetura, integracoes e multi-cluster

Data da investigacao: 2026-10-09.

Este documento separa observacoes realizadas no laboratorio de capacidades
descritas na documentacao oficial. Os modelos multi-cluster e multi-tenant abaixo
nao foram implantados nesta POC.

## 1. Objetivo e resultado

Objetivo: visualizar o fluxo `app-a -> app-b -> Redis`, com dependencias, tempos
e erros, e avaliar o Coroot como interface para uma stack existente.

| Item | Resultado observado |
|---|---|
| Duas apps HTTP e Redis no kind | Funcionando |
| Gerador de trafego | Requisicoes normais, lentas e erros intencionais |
| Service Map | Arestas load-generator -> app-a -> app-b -> Redis |
| Tracing OpenTelemetry | Mesmo trace_id entre as apps e cadeia pai/filho preservada |
| Redis no trace | Spans INCRBY, SET e GET |
| Duracao e erros no trace | Disponiveis |
| Latencia nas arestas do Service Map | Ausente |
| Captura eBPF L7 completa | Nao demonstrada; coleta limitada no kind |
| Consulta a Tempo/Loki existentes | Nao implementada; nao ha datasource nativo documentado para esse uso |

O endpoint `/checkout?scenario=slow` adiciona 600 ms no app-b.
O cenario `error` retorna 503 no app-b e 502 no app-a, antes de acessar Redis.

Ambiente observado: Linux com kernel 6.17, Docker local, kind 0.27.0,
Kubernetes 1.32.2, Coroot 1.27.1, node-agent 1.36.2 e cluster-agent 1.11.5.
Isso nao constitui garantia de compatibilidade com outros ambientes kind.

`verify.py` verificou respostas HTTP, cadeia dos spans armazenados e presenca
das arestas. Ele NAO exige estatisticas de latencia no mapa.
A ultima revisao do script completo `up.sh` teve sua execucao de revalidacao
interrompida; nao foi demonstrada uma recriacao limpa com essa revisao.

## 2. Como o Coroot coleta e armazena

```text
node-agent por no ---- metricas -----+
cluster-agent ------- metricas -----+--> Coroot --> Prometheus
                                    |
node-agent ---------- logs/profiles -+--> Coroot --> ClickHouse
apps com OTel ------- traces --------+

Coroot --> SQLite ou PostgreSQL: configuracao e incidentes
Coroot --> cache de metricas em disco
Coroot --> API e interface web
```

| Componente | Responsabilidade |
|---|---|
| Node-agent | Observa processos, containers, rede e protocolos suportados; coleta usando eBPF e outras fontes locais |
| Cluster-agent | Enriquece a observabilidade do cluster e realiza integracoes, incluindo bancos quando configurados |
| Prometheus ou compativel | Armazena metricas de recursos, conexoes, requisicoes e duracoes |
| ClickHouse | Armazena logs, traces e profiles; pode tambem armazenar metricas como alternativa ao Prometheus |
| SQLite | Banco de configuracao padrao, usado nesta POC |
| PostgreSQL | Alternativa para configuracao e incidentes; necessario para multiplas replicas Coroot |
| Cache local | Acelera consultas de metricas; cada replica Coroot mantem sua copia |

O Service Map e calculado a partir do modelo de aplicacoes/conexoes construido
com metricas e metadados. Nao depende de um banco de grafos separado.
A tela de tracing consulta spans armazenados no ClickHouse.

Na POC os volumes ficam dentro do node kind. Remover o cluster elimina os dados
de observabilidade. Redis foi configurado sem persistencia.

## 3. Origem da latencia no Service Map

O caminho nativo consultado no codigo usa metricas especificas, por exemplo:

```text
container_http_requests_total
container_http_requests_duration_seconds_total_sum
container_redis_queries_total
container_redis_queries_duration_seconds_total_sum
```

O Coroot associa origem/destino, agrega contagens e duracoes, e calcula a
duracao media das operacoes. Conceitualmente:

```text
duracao media = soma das duracoes / quantidade de operacoes
```

Essa medida nao e p95 e nao equivale ao RTT de rede.
O servidor nao usa automaticamente os spans OpenTelemetry para preencher as
arestas desse mapa.

### O que ocorreu no kind

O agente conseguiu reconhecer workloads e algumas conexoes persistentes, mas
registrou eventos com PID desconhecido. As arestas vieram com `stats: null`.
A consulta de metricas realizada encontrou `container_net_latency_seconds`,
mas nao as metricas L7 de requisicoes HTTP esperadas.

A explicacao oficial para ambientes Kubernetes aninhados em containers e a
diferenca entre PIDs vistos no kernel pelo eBPF e PIDs visiveis em `/proc`
no namespace do node. A observacao local e consistente com essa limitacao.

Reutilizar conexoes HTTP e reiniciar o agente depois de aquecer o trafego
permitiu descobrir as arestas. Isso NAO corrigiu a medicao L7 nem transforma
kind em um ambiente de suporte garantido para Coroot.

Para avaliar o mapa nativo com captura completa, a recomendacao e um cluster
com nodes Linux ou em VMs, sem esse aninhamento. Isso nao elimina a necessidade
de conferir suporte a kernel, runtime, protocolos e criptografia.

## 4. Reaproveitamento de dados existentes

| Fonte | Possibilidade | Restricao |
|---|---|---|
| Prometheus | Usar backend existente e dashboards PromQL | O mapa exige o modelo de metricas e labels esperado; metricas arbitrarias nao bastam |
| OpenTelemetry | Receber traces e logs por OTLP | Sao ingeridos no armazenamento do Coroot; nao substituem automaticamente a descoberta do mapa |
| Tempo | Nao foi encontrada integracao nativa documentada para consulta | Cadastrar sua URL nao transforma o Coroot em uma interface para os traces existentes |
| Loki | Nao foi encontrada integracao nativa documentada para consulta | O caminho documentado de logs usa ClickHouse |
| Istio | Consultar suas metricas em dashboards no Prometheus | Nao foi encontrada traducao nativa documentada das metricas Istio para o mapa Coroot |

E possivel enviar os traces ja instrumentados para dois destinos:

```text
                    +--> Tempo
OTel Collector -----+
                    +--> Coroot --> ClickHouse
```

Isso reaproveita instrumentacao/coleta, mas duplica ingestao e armazenamento.
Nao importa automaticamente o historico do Tempo.

Em principio, uma integracao customizada poderia produzir metricas compativeis
com o modelo do Coroot sem eBPF. Essa alternativa nao foi implementada nem
validada: envolve identidades, metadados, protocolos, contadores, duracoes e
associacao entre conexoes. Apenas renomear metricas nao garante equivalencia.

Para uma camada de visualizacao sobre Tempo, Prometheus e Loki existentes,
Grafana + Tempo Service Graph e o encaixe recomendado nesta investigacao.
Requer traces adequados e geracao de metricas de service graph; nao basta ter
traces historicos armazenados. Essa alternativa nao foi instalada nesta POC.

## 5. Multi-cluster centralizado

Nao e necessario um Coroot completo em cada cluster:

```text
Cluster A: node-agent por no + cluster-agent -- chave A --+
                                                        +--> Coroot central
Cluster B: node-agent por no + cluster-agent -- chave B --+       |
                                                    armazenamentos centrais
```

Usando a instalacao gerenciada, cada cluster remoto tambem tem o operador.
O modo `spec.agentsOnly.corootURL` direciona os agentes ao Coroot central.
Nao exige instalar Prometheus, ClickHouse e Coroot server em cada cluster remoto.

Organizacao inicial recomendada:

```text
prod-br      <- telemetria do cluster Brasil, chave propria
prod-us      <- telemetria do cluster EUA, chave propria
prod-global  <- memberProjects: [prod-br, prod-us]
```

No modo multi-tenancy com armazenamentos globais:

- Coroot acrescenta `coroot_project_id` nas metricas e aplica esse filtro nas
  consultas ao Prometheus.
- ClickHouse tem uma database dedicada por projeto.
- Projetos agregados consultam os membros sob demanda; nao recebem ingestao
  propria nem duplicam o pipeline.
- Os membros devem ser projetos regulares; nao se aninham projetos agregados.

Uma visao agregada nao garante identificar cada chamada cross-cluster:
gateways, proxies e NAT podem dificultar a resolucao do destino.
Traces ponta a ponta ainda dependem de propagacao de contexto entre servicos.

Para alta disponibilidade do Coroot central, usar PostgreSQL para configuracao
e incidentes, alem de dimensionar os armazenamentos e caches. TLS, autenticacao
e politica de acesso devem substituir as configuracoes permissivas da POC.

## 6. Multi-cluster com armazenamento descentralizado

Quando os dados precisam permanecer em cada regiao/cluster:

```text
Cluster A: Coroot + armazenamento local --+
                                         +--> Coroot agregador --> UI global
Cluster B: Coroot + armazenamento local --+
```

O agregador referencia as instancias com `remoteCoroot` e pode combinar projetos
com `memberProjects`. A documentacao descreve a consulta remota mantendo os
dados nos armazenamentos locais. E um desenho diferente do modelo agentsOnly.

## 7. Multi-tenant: infraestrutura versus clientes de negocio

**Clusters/projetos separados:** chaves e projetos permitem organizar a
ingestao e as consultas. Nao enviar todos os clusters indiscriminadamente para
o mesmo projeto se for necessario preservar essa separacao.

**Tenants com workloads separados:** namespaces e identidades de workload
ajudam a distinguir aplicacoes. A coleta por no pode abranger varios tenants;
filtros visuais nao sao fronteiras de seguranca. Isolamento de ingestao exige
planejar escopo e encaminhamento dos dados.

**Tenants compartilhando os mesmos pods:** eBPF observa os mesmos processos e
conexoes, nao a identidade de negocio de cada cliente. Instrumentacao pode
adicionar atributos de trace como `tenant.id` e `business.feature`. Esses
atributos nao criam automaticamente um mapa por cliente nem isolamento de
autorizacao. Controlar cardinalidade e evitar dados pessoais na telemetria.

Na Community os papeis Admin, Editor e Viewer sao fixos. RBAC granular por
projeto/aplicacao e documentado como Enterprise. Uma interface oferecida a
clientes externos requer avaliar autorizacao e isolamento de armazenamento;
nao assumir que criar projetos basta.

## 8. Fontes e limites das conclusoes

Documentacao e codigo consultados em 2026-10-09. Links `main` e documentacao
corrente podem mudar; os resultados experimentais acima se referem a POC.

- [Arquitetura](https://docs.coroot.com/installation/architecture)
- [Requisitos e restricoes de ambientes aninhados](https://docs.coroot.com/installation/requirements)
- [Explicacao dos namespaces de PID](https://github.com/coroot/coroot/issues/10#issuecomment-1345201225)
- [Consultas de metricas do mapa](https://github.com/coroot/coroot/blob/main/constructor/queries.go)
- [Calculo da latencia](https://github.com/coroot/coroot/blob/main/model/connection.go)
- [Renderizacao do Service Map](https://github.com/coroot/coroot/blob/main/api/views/overview/service_map.go)
- [Prometheus](https://docs.coroot.com/configuration/prometheus)
- [Multi-tenancy](https://docs.coroot.com/configuration/multi-tenancy)
- [Multi-cluster](https://docs.coroot.com/configuration/multi-cluster)
- [Operador e agentsOnly](https://docs.coroot.com/installation/k8s-operator)
- [Armazenamento descentralizado](https://docs.coroot.com/guides/multi-cluster)
- [Banco de configuracao](https://docs.coroot.com/configuration/database)
- [Alta disponibilidade](https://docs.coroot.com/configuration/high-availability)
- [RBAC Community e Enterprise](https://docs.coroot.com/configuration/rbac)
- [Tempo Service Graph](https://grafana.com/docs/tempo/latest/metrics-from-traces/service_graphs/)
