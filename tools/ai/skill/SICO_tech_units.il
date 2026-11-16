; Dump authoritative units/grid from the OA technology database.
; Usage: sicoTechUnitsDump("tsmcN65" "/path/to/units.txt")
; Parse with cadai.pdk_standard.tech_units.parse_skill_probe.
procedure(sicoTechUnitsDump(libName outputPath)
  let((lib tf out)
    lib = ddGetObj(libName)
    unless(lib
      error("sicoTechUnitsDump: library not found: %s" libName))
    tf = techGetTechFile(lib)
    unless(tf
      error("sicoTechUnitsDump: technology database not found: %s" libName))
    out = outfile(outputPath)
    unless(out
      error("sicoTechUnitsDump: cannot open output: %s" outputPath))
    fprintf(out "tech_lib=%s\n" libName)
    fprintf(out "viewTypeUnits=%L\n" techGetViewTypeUnits(tf))
    fprintf(out "dbuPerUU=%L\n" techGetDBUPerUU(tf "maskLayout"))
    fprintf(out "userUnit=%L\n" techGetUserUnit(tf "maskLayout"))
    fprintf(out "mfgGrid=%L\n" techGetMfgGridResolution(tf))
    close(out)
    t
  )
)
