; Source-entry compatibility names delegate to the one source loader.
; These helpers are excluded from protected contexts.
unless(and(boundp('sicoSourceApiVersion)
           sicoSourceApiVersion=="20261017.source.api.v1")

procedure(aiResolveRoot()
  strcat(SICO_sourceRoot() "/tools/ai"))

procedure(aiLoadFrontend()
  load(strcat(SICO_sourceRoot() "/tools/ai/skill++/AI.ils")))

procedure(aiLoaderRevision()
  "20260924.ai.loader.v34")

procedure(sicoLoaderRevision()
  "20260924.sico.loader.v6")

procedure(drcLoaderRevision()
  SICO_sourceRevision("drc"))

procedure(lvsLoaderRevision()
  SICO_sourceRevision("lvs"))

procedure(rceLoaderRevision()
  SICO_sourceRevision("rce"))

procedure(lefLoaderRevision()
  SICO_sourceRevision("lef"))

sicoSourceApiVersion="20261017.source.api.v1"
)
