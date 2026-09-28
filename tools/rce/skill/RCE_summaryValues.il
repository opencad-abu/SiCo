; Normalize RCE summary strings, paths and readiness.

unless(and(boundp('RCEsummaryValuesVersion)
             RCEsummaryValuesVersion=="20260919.gui.modules.v1")
procedure(rceSummaryString(value)
  cond(
    (null(value) "")
    (stringp(value) value)
    (symbolp(value) get_pname(value))
    (t sprintf(nil "%L" value))
  )
)

procedure(rceSummaryFileReady(path)
  and(path stringp(path) strlen(path)>0 isFile(path) fileLength(path)>0)
)

procedure(rceSummaryPath(value)
  let((path)
    path=rceSummaryString(value)
    if(and(strlen(path)>0 isCallable('SICO_absolutePath)) then
      SICO_absolutePath(path)
    else
      path
    )
  )
)

procedure(rceSummaryLcv(libName cellName viewName)
  sprintf(nil "%s/%s/%s"
           rceSummaryString(libName)
           rceSummaryString(cellName)
           rceSummaryString(viewName))
)

procedure(rceSummaryJoin(values separator)
  let((result prefix value)
    result=""
    prefix=""
    foreach(value values
      result=strcat(result prefix rceSummaryString(value))
      prefix=separator
    )
    result
  )
)

procedure(rceSummaryNormalizedPaths(outputPaths)
  let((paths path)
    paths=nil
    foreach(path outputPaths
      when(strlen(rceSummaryString(path))>0
        paths=append1(paths rceSummaryPath(path)))
    )
    paths
  )
)

procedure(rceSummaryPathsReady(paths)
  let((ready path)
    ready=and(paths t)
    foreach(path paths
      unless(rceSummaryFileReady(path)
        ready=nil))
    ready
  )
)

procedure(rceSummaryPathSizes(paths ready)
  let((sizes path)
    sizes=nil
    when(ready
      foreach(path paths
        sizes=append1(sizes sprintf(nil "%L bytes" fileLength(path)))))
    rceSummaryJoin(sizes "\n")
  )
)

procedure(rceSummaryFirstLine(path)
  let((input line)
    when(and(path isFile(path))
      input=infile(path)
      when(input
        gets(line input)
        close(input)
      )
    )
    or(line "")
  )
)

RCEsummaryValuesVersion="20260919.gui.modules.v1"
)
