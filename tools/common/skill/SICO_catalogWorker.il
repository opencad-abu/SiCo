  procedure(cadviewCatalogHexByte(value)
    let((digits high low)
      digits="0123456789abcdef"
      high=quotient(value 16)
      low=mod(value 16)
      strcat(getchar(digits high+1) getchar(digits low+1))
    )
  )
  procedure(cadviewCatalogJsonString(value)
    let((text out index character code)
      text=if(value then sprintf(nil "%s" value) else "")
      out="\""
      for(index 1 strlen(text)
        character=substring(text index 1)
        code=charToInt(getchar(text index))
        cond(
          (character=="\\" out=strcat(out "\\\\"))
          (character=="\"" out=strcat(out "\\\""))
          (character=="\n" out=strcat(out "\\n"))
          (character=="\r" out=strcat(out "\\r"))
          (character=="\t" out=strcat(out "\\t"))
          (code==8 out=strcat(out "\\b"))
          (code==12 out=strcat(out "\\f"))
          (and(numberp(code) code<32)
            out=strcat(out "\\u00" cadviewCatalogHexByte(code)))
          (t out=strcat(out character))
        )
      )
      strcat(out "\"")
    )
  )
  procedure(cadviewCatalogWriteCategory(catalogPort parent categoryName firstCategory activeCategories)
    let((categoryResult category membersResult members member memberName memberType firstMember firstChild wrote)
      wrote=nil
      categoryResult=when(isCallable('ddCatOpen)
        errset(apply('ddCatOpen list(parent categoryName "r")) nil))
      category=when(categoryResult car(categoryResult))
      when(category
        unless(or(member(category activeCategories)
                  length(activeCategories)>=64)
          membersResult=when(isCallable('ddCatGetCatMembers)
            errset(apply('ddCatGetCatMembers list(category)) nil))
          when(membersResult
            members=car(membersResult)
            unless(firstCategory fprintf(catalogPort ","))
            fprintf(catalogPort "{\"name\":%s,\"members\":["
              cadviewCatalogJsonString(categoryName)
            )
            firstMember=t
            foreach(member members
              when(and(listp(member) car(member) cdr(member))
                memberName=car(member)
                memberType=sprintf(nil "%s" cadr(member))
                when(equal(memberType "cell")
                  unless(firstMember fprintf(catalogPort ","))
                  firstMember=nil
                  fprintf(catalogPort "%s"
                    cadviewCatalogJsonString(memberName)
                  )
                )
              )
            )
            fprintf(catalogPort "],\"children\":[")
            firstChild=t
            foreach(member members
              when(and(listp(member) car(member) cdr(member))
                memberName=car(member)
                memberType=sprintf(nil "%s" cadr(member))
                when(equal(memberType "category")
                  when(cadviewCatalogWriteCategory(
                      catalogPort category memberName firstChild
                      cons(category activeCategories))
                    firstChild=nil
                  )
                )
              )
            )
            fprintf(catalogPort "]}")
            wrote=t
          )
        )
        when(isCallable('ddCatClose)
          errset(apply('ddCatClose list(category)) nil)
        )
      )
      wrote
    )
  )

procedure(cadRuntimeCatalog()
let((catalogFile catalogPort)
  catalogFile=getShellEnvVar("CADVIEW_CATALOG_OUTPUT")
  catalogPort=outfile(catalogFile "w")
  fprintf(catalogPort "{\"schema_version\":1,\"authoritative\":true,\"provider\":\"dbAccess\",\"combine_groups\":[")
  let((firstGroup groupLib groupMembers member)
    firstGroup=t
    foreach(groupLib ddGetLibList()
      groupMembers=when(isCallable('ddGetCombineValue)
        errset(apply('ddGetCombineValue list(groupLib)) nil))
      groupMembers=when(and(groupMembers car(groupMembers)) car(groupMembers))
      when(and(groupMembers car(groupMembers))
        unless(firstGroup fprintf(catalogPort ","))
        firstGroup=nil
        fprintf(catalogPort "{\"name\":%s,\"members\":["
          cadviewCatalogJsonString(ddGetObjName(groupLib))
        )
        let((firstMember)
          firstMember=t
          foreach(member groupMembers
            unless(firstMember fprintf(catalogPort ","))
            firstMember=nil
            fprintf(catalogPort "%s" cadviewCatalogJsonString(member))
          )
        )
        fprintf(catalogPort "]}")
      )
    )
  )
  fprintf(catalogPort "],\"libraries\":[")
  let((firstLib lib libName readPath writePath combineMembers categories firstCell cellId cellName firstView viewId viewName)
    firstLib=t
    foreach(lib ddGetLibList()
      libName=ddGetObjName(lib)
      readPath=errset(ddGetObjReadPath(lib) nil)
      readPath=when(and(readPath car(readPath)) car(readPath))
      writePath=errset(ddGetObjWritePath(lib) nil)
      writePath=when(and(writePath car(writePath)) car(writePath))
      combineMembers=when(isCallable('ddGetCombineValue)
        errset(apply('ddGetCombineValue list(lib)) nil))
      combineMembers=when(and(combineMembers car(combineMembers)) car(combineMembers))
      categories=when(isCallable('ddCatGetLibCats)
        errset(apply('ddCatGetLibCats list(lib)) nil))
      categories=when(and(categories car(categories)) car(categories))
      when(and(libName readPath)
        unless(firstLib fprintf(catalogPort ","))
        firstLib=nil
        fprintf(catalogPort "{\"name\":%s,\"read_path\":%s,\"writable\":%s,\"combine_members\":["
          cadviewCatalogJsonString(libName)
          cadviewCatalogJsonString(readPath)
          if(writePath then "true" else "false")
        )
        let((firstCombine member)
          firstCombine=t
          foreach(member combineMembers
            unless(firstCombine fprintf(catalogPort ","))
            firstCombine=nil
            fprintf(catalogPort "%s" cadviewCatalogJsonString(member))
          )
        )
        fprintf(catalogPort "],\"categories\":[")
        let((firstCategory categoryName)
          firstCategory=t
          foreach(categoryName categories
            when(cadviewCatalogWriteCategory(
                catalogPort lib categoryName firstCategory nil)
              firstCategory=nil
            )
          )
        )
        fprintf(catalogPort "],\"cells\":[")
        firstCell=t
        foreach(cellId ddGetObjChildren(lib)
          when(equal(ddGetObjType(cellId) 'ddCellType)
            cellName=ddGetObjName(cellId)
            when(cellName
              unless(firstCell fprintf(catalogPort ","))
              firstCell=nil
              fprintf(catalogPort "{\"name\":%s,\"views\":["
                cadviewCatalogJsonString(cellName)
              )
              firstView=t
              foreach(viewId ddGetObjChildren(cellId)
                when(equal(ddGetObjType(viewId) 'ddViewType)
                  viewName=ddGetObjName(viewId)
                  when(viewName
                    unless(firstView fprintf(catalogPort ","))
                    firstView=nil
                    fprintf(catalogPort "%s" cadviewCatalogJsonString(viewName))
                  )
                )
              )
              fprintf(catalogPort "]}")
            )
          )
        )
        fprintf(catalogPort "]}")
      )
    )
  )
  fprintf(catalogPort "]}\n")
  close(catalogPort)
)
t)
