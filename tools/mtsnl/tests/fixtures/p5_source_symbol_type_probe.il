; Small source-side probe for the documented nil view-type form.
let((cv)
  cv = dbOpenCellViewByType(
    getShellEnvVar("MTS_SOURCE_LIB")
    getShellEnvVar("MTS_SOURCE_CELL")
    "symbol"
    nil
    "r"
  )
  printf("MTS_P5_NIL_TYPE_OPEN=%L\n" cv)
  when(cv dbClose(cv))
)
exit()
