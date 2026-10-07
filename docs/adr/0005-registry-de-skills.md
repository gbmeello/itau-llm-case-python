# ADR-0005: Registry de skills versionado (prompts, políticas, exemplos)

- **Status:** aceito · **Data:** 2026-10-06

## Contexto
O case pede CRUD de skills/artefatos de IA com rastreabilidade. Prompts e políticas mudam com mais frequência que o código e precisam de rollback e de auditoria ("qual prompt gerou esta decisão?").

## Decisão
- Skills como dados versionados (`SkillVersionEntity`): `skillId`, tipo (`PROMPT`, `POLICY`, `EXAMPLES`), semver, status (`DRAFT`, `ACTIVE`, `DEPRECATED`, `DELETED`), conteúdo, SHA-256, changelog e autor.
- **Imutabilidade:** update = nova versão; uma `ACTIVE` por skill; rollback = reativar uma versão anterior.
- **Soft delete:** remoção vira `DELETED`; skills essenciais são protegidas (409).
- **Fonte inicial em git** (`resources/skills/...`), carregada pelo seeder; evolução em runtime via API.
- Cada `PurchaseDecision.audit.skillVersions` registra as versões usadas.

## Alternativas
- **Prompts como constantes no código:** exigem deploy para mudar e não têm rollback em runtime. Rejeitada.
- **Só arquivos em git:** boa rastreabilidade, mas sem ativação/rollback em runtime. Mantido como fonte inicial (o melhor dos dois).
- **Plataforma externa de prompts:** dependência extra, desnecessária no escopo.

## Consequências
- (+) Rollback instantâneo, trilha completa, ativação condicionada a eval (processo).
- (−) Em SQLite em memória (default), o estado de runtime se perde ao reiniciar (o seed recria). Em produção, usar `DATABASE_URL` apontando para PostgreSQL. O gate de eval antes de ativar é processo, não está imposto em código. Evolução: exigir um ID de relatório de eval aprovado na ativação.
