; Bounded native CDF results over the existing eval bridge. No request paths.
unless(boundp('aiCdfTransfers) aiCdfTransfers=makeTable("aiCdfTransfers" nil))
unless(boundp('aiCdfTransferCount) aiCdfTransferCount=0)
procedure(aiCdfPut(actText actToken)
  unless(and(stringp(actText) strlen(actText)<=2097152) error("CDF result exceeds 2 MiB"))
  when(or(aiCdfTransferCount>=16 aiCdfTransfers[actToken]) error("CDF transfer limit/conflict"))
  aiCdfTransfers[actToken]=actText aiCdfTransferCount=aiCdfTransferCount+1
  aiCircuitObject(list(list("ok" t) list("bytes" strlen(actText)))))
procedure(aiCdfRead(actToken actOffset)
  prog((actText actEnd actHex actByte)
    actText=aiCdfTransfers[actToken]
    unless(and(actText fixp(actOffset) actOffset>=0 actOffset<strlen(actText))
      return(aiCircuitError("cdf_transfer_missing" "Unknown CDF transfer/offset")))
    actHex="" actEnd=min(actOffset+6000 strlen(actText))
    for(actIndex actOffset+1 actEnd
      actByte=charToInt(concat(substring(actText actIndex 1)))
      actHex=strcat(actHex sprintf(nil "%02x" actByte)))
    return(aiCircuitObject(list(list("ok" t) list("hex" actHex))))))
procedure(aiCdfDrop(actToken)
  when(aiCdfTransfers[actToken]
    aiCdfTransfers[actToken]=nil aiCdfTransferCount=aiCdfTransferCount-1)
  aiCircuitObject(list(list("ok" t))))
