#!/usr/bin/env perl
# Gera postman/purchase-agent.postman_collection.json a partir de examples/*.json.
# Uso: perl scripts/build_postman.pl
use strict; use warnings; use utf8; use JSON::PP;

my $json = JSON::PP->new->utf8->canonical->pretty;
my $root = do { (my $d = __FILE__) =~ s{[\\/]scripts[\\/][^\\/]+$}{}; $d eq __FILE__ ? '.' : $d };

sub example {
    my ($file, $request_id) = @_;
    open my $f, '<:raw', "$root/examples/$file" or die "$file: $!";
    local $/; my $data = JSON::PP->new->utf8->decode(<$f>); close $f;
    $data->{requestId} = $request_id;
    return JSON::PP->new->utf8(0)->canonical->pretty->encode($data);
}

sub tests { return { listen => 'test', script => { type => 'text/javascript', exec => [split /\n/, shift] } } }

sub req {
    my (%a) = @_;
    my $r = {
        name => $a{name},
        request => {
            method => $a{method},
            header => [ ($a{body} ? { key => 'Content-Type', value => 'application/json' } : ()),
                        ($a{no_auth} ? () : { key => 'X-API-Key', value => '{{apiKey}}' }),
                        @{ $a{headers} || [] } ],
            url => { raw => "{{baseUrl}}$a{path}", host => ['{{baseUrl}}'],
                     path => [ grep { length } split m{/}, (split /\?/, $a{path})[0] ],
                     ($a{path} =~ /\?(.+)/ ? (query => [ map { my ($k, $v) = split /=/; { key => $k, value => $v } } split /&/, $1 ]) : ()) },
            ($a{body} ? (body => { mode => 'raw', raw => $a{body}, options => { raw => { language => 'json' } } }) : ()),
            ($a{description} ? (description => $a{description}) : ()),
        },
        event => [ tests($a{tests}) ],
    };
    return $r;
}

my $decision_test = sub {
    my ($expected, $extra) = @_;
    return <<"JS" . ($extra // '');
pm.test("HTTP 200", () => pm.response.to.have.status(200));
const d = pm.response.json();
pm.test("decision = $expected", () => pm.expect(d.decision).to.eql("$expected"));
pm.test("toda razão cita evidência existente", () => {
  const ids = new Set(d.evidence.map(e => e.id));
  d.reasons.forEach(r => { pm.expect(r.evidenceIds.length).to.be.above(0); r.evidenceIds.forEach(i => pm.expect(ids.has(i), i).to.be.true); });
});
pm.collectionVariables.set("decisionId", d.audit.decisionId);
console.log(d.decision, d.audit.decidedBy, d.summary);
JS
};

my @folders = (
  { name => '0 · Saúde e borda', item => [
    req(name => 'Health', method => 'GET', path => '/health', no_auth => 1,
        tests => qq{pm.test("UP", () => pm.expect(pm.response.json().status).to.eql("UP"));}),
    req(name => 'Sem API key → 401', method => 'POST', path => '/v1/purchase-requests/evaluate', no_auth => 1,
        body => '{}', tests => qq{pm.test("401", () => pm.response.to.have.status(401));}),
    req(name => 'Contrato violado → 400', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => qq({\n  "requestId": "X",\n  "items": []\n}),
        tests => qq{pm.test("400 CONTRACT_VIOLATION", () => { pm.response.to.have.status(400); pm.expect(pm.response.json().code).to.eql("CONTRACT_VIOLATION"); });}),
  ]},
  { name => '1 · Cenários de decisão', item => [
    req(name => '01 · Aprovação automática', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => example('01-approve.json', 'PM-{{runId}}-01'),
        tests => $decision_test->('APPROVE', qq{pm.test("AUTO_APPROVE no ERP", () => pm.expect(d.erpPayload.action).to.eql("AUTO_APPROVE"));\n})),
    req(name => '02 · 3x acima da média → humano', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => example('02-escalate-above-history.json', 'PM-{{runId}}-02'), tests => $decision_test->('ESCALATE_TO_HUMAN')),
    req(name => '03 · Sem fornecedor → NEEDS_INFO (abre caso)', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => example('03-needs-info.json', 'PM-{{runId}}-03'),
        tests => $decision_test->('NEEDS_INFO', qq{pm.test("abriu caso", () => pm.expect(d.caseId).to.be.a("string"));\npm.collectionVariables.set("caseId", d.caseId);\n})),
    req(name => '04 · Fornecedor bloqueado → regra, sem LLM', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => example('04-blocked-supplier.json', 'PM-{{runId}}-04'),
        tests => $decision_test->('REJECT', qq{pm.test("RULE_ENGINE e zero chamadas ao LLM", () => { pm.expect(d.audit.decidedBy).to.eql("RULE_ENGINE"); pm.expect(d.audit.llmCalls).to.eql(0); });\n})),
    req(name => '05 · Prompt injection → humano', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => example('05-prompt-injection.json', 'PM-{{runId}}-05'),
        tests => $decision_test->('ESCALATE_TO_HUMAN', qq{pm.test("SUSPICIOUS_INPUT detectado", () => pm.expect(d.dataQuality.issues).to.include("SUSPICIOUS_INPUT"));\n})),
    req(name => '06 · LLM fora do ar → FALLBACK', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => example('06-llm-outage-fake-only.json', 'PM-{{runId}}-FAKE-DOWN'),
        description => 'O marcador FAKE-DOWN no requestId simula o provedor sobrecarregado (só no LLM fake).',
        tests => $decision_test->('ESCALATE_TO_HUMAN', qq{pm.test("FALLBACK", () => pm.expect(d.audit.decidedBy).to.eql("FALLBACK"));\n})),
    req(name => '07 · Fracionamento de compra → humano', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => example('07-split-purchase.json', 'PM-{{runId}}-07'),
        tests => $decision_test->('ESCALATE_TO_HUMAN', qq{pm.test("SPLIT_PURCHASE_SUSPECTED", () => pm.expect(d.reasons.map(r => r.code)).to.include("SPLIT_PURCHASE_SUSPECTED"));\n})),
    req(name => '08 · PII na justificativa é mascarada', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => qq({\n  "requestId": "PM-{{runId}}-PII",\n  "requester": { "costCenter": "CC-4410", "name": "Joao Silva" },\n  "supplier": { "taxId": "11222333000181", "name": "Acme Hardware Ltda" },\n  "items": [ { "sku": "MOU-01", "description": "Mouses", "category": "IT_HARDWARE", "quantity": 2, "unitPrice": 150 } ],\n  "justification": "Mouses para a equipe. Contato: joao\@empresa.com, CPF 529.982.247-25, tel (11) 91234-5678"\n}),
        tests => qq{pm.test("HTTP 200", () => pm.response.to.have.status(200));\nconst d = pm.response.json();\npm.test("PII mascarada", () => pm.expect(d.dataQuality.issues.join()).to.include("PII_MASKED"));\npm.test("CPF e e-mail não aparecem", () => { pm.expect(pm.response.text()).to.not.include("529.982.247-25"); pm.expect(pm.response.text()).to.not.include("joao\@empresa.com"); });}),
    req(name => '09 · Idempotência (envie 2x: na 2ª, replay=true)', method => 'POST', path => '/v1/purchase-requests/evaluate',
        body => example('01-approve.json', 'PM-IDEMPOTENCIA'),
        description => 'Mesmo requestId e mesmo conteúdo: a 2ª chamada devolve a decisão gravada, sem chamar o LLM.',
        tests => qq{pm.test("HTTP 200", () => pm.response.to.have.status(200));\nconsole.log("X-Idempotent-Replay:", pm.response.headers.get("X-Idempotent-Replay"));}),
  ]},
  { name => '2 · Caso multi-turno', item => [
    req(name => 'Responder o caso (envie antes o 03)', method => 'POST', path => '/v1/cases/{{caseId}}/messages',
        body => qq({\n  "message": "Fornecedor: Escritorio Total, homologado para mobiliario.",\n  "updates": { "supplier": { "taxId": "78124569000156", "name": "Escritorio Total ME" } }\n}),
        tests => qq{pm.test("HTTP 200", () => pm.response.to.have.status(200));\nconst d = pm.response.json();\npm.test("caso resolvido", () => pm.expect(d.decision).to.not.eql("NEEDS_INFO"));\nconsole.log(d.decision, d.summary);}),
    req(name => 'Estado do caso', method => 'GET', path => '/v1/cases/{{caseId}}',
        tests => qq{pm.test("HTTP 200", () => pm.response.to.have.status(200));}),
  ]},
  { name => '3 · Auditoria', item => [
    req(name => 'Decisão por ID (última avaliada)', method => 'GET', path => '/v1/decisions/{{decisionId}}',
        tests => qq{pm.test("traz contexto incluído e cortado", () => { const a = pm.response.json(); pm.expect(a).to.have.property("contextIncluded"); pm.expect(a).to.have.property("contextExcluded"); });}),
    req(name => 'Histórico de uma solicitação', method => 'GET', path => '/v1/decisions?requestId=PM-IDEMPOTENCIA',
        tests => qq{pm.test("HTTP 200", () => pm.response.to.have.status(200));}),
  ]},
  { name => '4 · Skills (CRUD versionado)', item => [
    req(name => 'Listar skills', method => 'GET', path => '/v1/skills',
        tests => qq{pm.test("analyst ativo", () => pm.expect(JSON.stringify(pm.response.json().analyst)).to.include("ACTIVE"));}),
    req(name => 'Versões do analyst (com conteúdo)', method => 'GET', path => '/v1/skills/analyst',
        tests => qq{pm.test("HTTP 200", () => pm.response.to.have.status(200));}),
    req(name => 'Criar skill opcional', method => 'POST', path => '/v1/skills', headers => [ { key => 'X-User', value => 'postman' } ],
        body => qq({\n  "skillId": "pm-examples-{{runId}}",\n  "type": "EXAMPLES",\n  "version": "1.0.0",\n  "content": "Exemplo criado pelo Postman",\n  "changelog": "teste via Postman"\n}),
        tests => qq{pm.test("201", () => pm.response.to.have.status(201));\npm.collectionVariables.set("tmpSkill", pm.response.json().skillId);}),
    req(name => 'Remover skill opcional (soft delete)', method => 'DELETE', path => '/v1/skills/{{tmpSkill}}',
        tests => qq{pm.test("204", () => pm.response.to.have.status(204));}),
    req(name => 'Remover skill essencial → 409', method => 'DELETE', path => '/v1/skills/analyst',
        tests => qq{pm.test("409", () => pm.response.to.have.status(409));}),
    req(name => 'Rollback: ativar analyst 1.0.0', method => 'POST', path => '/v1/skills/analyst/versions/1.0.0/activate',
        description => 'Depois rode "Voltar para analyst 1.1.0".',
        tests => qq{pm.test("ACTIVE", () => pm.expect(pm.response.json().status).to.eql("ACTIVE"));}),
    req(name => 'Voltar para analyst 1.1.0', method => 'POST', path => '/v1/skills/analyst/versions/1.1.0/activate',
        tests => qq{pm.test("ACTIVE", () => pm.expect(pm.response.json().status).to.eql("ACTIVE"));}),
  ]},
  { name => '5 · Observabilidade', item => [
    req(name => 'Métricas Prometheus', method => 'GET', path => '/metrics', no_auth => 1,
        tests => qq{pm.test("métricas do agente", () => pm.expect(pm.response.text()).to.include("agent_decisions_total"));}),
  ]},
);

my $collection = {
  info => {
    name => 'Purchase Approval Agent (case Itaú)',
    description => "API do agente de aprovação de compras. Rode 'Run collection' para executar tudo com testes automáticos.\n"
                 . "Ordem sugerida: pastas 0 → 5 (o caso multi-turno usa o caseId salvo pelo cenário 03).",
    schema => 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
  },
  variable => [
    { key => 'baseUrl', value => 'http://localhost:8080' },
    { key => 'apiKey', value => 'dev-key-change-me' },
    { key => 'caseId', value => '' },
    { key => 'decisionId', value => '' },
    { key => 'tmpSkill', value => '' },
  ],
  event => [ { listen => 'prerequest', script => { type => 'text/javascript',
              exec => [ '// requestId único por envio (evita o replay da idempotência)', 'pm.variables.set("runId", Date.now());' ] } } ],
  item => \@folders,
};

mkdir "$root/postman";
open my $out, '>:raw', "$root/postman/purchase-agent.postman_collection.json" or die $!;
print $out $json->encode($collection);
close $out;
print "ok\n";
