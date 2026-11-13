# Agenti v samostatných kontejnerech, Postgres jako sběrnice i sdílená tabule

Každý ze šesti agentů (řídicí, čtyři sběrní, analytický) běží ve vlastním kontejneru se společným Docker image, má vlastní roli, nástroje a volání LLM a s ostatními mluví jen zprávami. Zprávy nesou druh podle FIPA ACL (Zadání = REQUEST, Nález = INFORM, Dotaz = QUERY, Selhání = FAILURE), odesílatele, příjemce a ID konverzace. Zprávy i nálezy ukládáme do jednoho Postgresu: zprávy jsou řádky v tabulce, agenti se o nových dozvídají přes `LISTEN/NOTIFY`, a nálezy tvoří sdílenou tabuli (blackboard), ze které čte analytický agent. Chceme, aby prototyp byl multiagentní systém i podle učebnicové definice (autonomní agenti, komunikace jen zprávami, žádná sdílená paměť), a zároveň jen s jednou infrastrukturní službou navíc, protože na implementaci je šest dní.

## Considered Options

- **Všichni agenti v jednom procesu** (třídy volané orchestrátorem): nejjednodušší, ale agenti by sdíleli paměť a „multi“ by bylo jen v názvu.
- **Redis Streams nebo RabbitMQ jako sběrnice + Postgres na data**: komunikace je na diagramu vidět víc, ale je to další kontejner a log komunikace pro web bychom stejně museli ukládat do Postgresu.
- **Frameworky CrewAI, AutoGen, LangGraph**: agenti v nich běží v jednom procesu, takže distribuovanost by se ztratila; navíc závislost, kterou tým nezná.

## Consequences

- Postgres je jediný bod selhání: když neběží, nekomunikuje nikdo. Pro prototyp přijatelné.
- Log komunikace na webu je přímo tabulka zpráv, nic se nesynchronizuje.
- Výpadek jednoho agenta ostatní nezastaví; řídicí agent po 30 minutách bez odpovědi zapíše Selhání a Brief mezeru přizná.
