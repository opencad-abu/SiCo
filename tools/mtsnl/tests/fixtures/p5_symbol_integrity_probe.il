; P5 read-only semantic report for source/transfer/target symbol views.
; Set MTS_PROBE_LIB and MTS_PROBE_CELL in the process environment.
let((libName cellName cv)
  libName = getShellEnvVar("MTS_PROBE_LIB")
  cellName = getShellEnvVar("MTS_PROBE_CELL")
  cv = dbOpenCellViewByType(libName cellName "symbol" nil "r")
  unless(cv error("MTS symbol integrity probe cannot open cellview"))
  printf("MTS_P5_CV=%s/%s/symbol\n" libName cellName)
  printf("MTS_P5_CELLVIEW_PROPS=%L\n" foreach(mapcar prop cv~>prop prop~>name))
  printf("MTS_P5_TERMINALS=%L\n" foreach(mapcar terminal cv~>terminals terminal~>name))
  printf("MTS_P5_SHAPES=%L\n" length(cv~>shapes))
  foreach(shape cv~>shapes
    printf("MTS_P5_SHAPE type=%L layer=%L purpose=%L bbox=%L name=%L\n"
      shape~>objType shape~>layerName shape~>purpose shape~>bBox shape~>name)
  )
  foreach(term cv~>terminals
    printf("MTS_P5_TERM name=%L direction=%L net=%L pins=%L\n"
      term~>name term~>direction term~>net~>name
      foreach(mapcar pin term~>pins pin~>name))
  )
  dbClose(cv)
)
exit()
