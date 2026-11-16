# SiCo DataHub v1 Tcl wire adapter.
# Standard Tcl 8.6 commands only; Calibre may use Tcllib json as a fast path.

namespace eval ::sico::datahub {
    variable maxEnvelope 65536
    variable maxPayload 55000
    variable maxText 4096
    variable maxId 128
    variable maxDeadline 300000
    variable jsonReady
    variable readCounter 0

    set scriptDirectory [file dirname [info script]]
    set contractFile [file join $scriptDirectory datahub_contracts.tcl]
    if {![file isfile $contractFile]} {
        error "DataHub generated contract file is missing: $contractFile"
    }
    source $contractFile

    proc fail {code message} {
        return -code error -errorcode [list SICO_DATAHUB $code] $message
    }

    proc revision {} {
        return $::datahubContractRevision
    }

    proc json_available {} {
        variable jsonReady
        if {![info exists jsonReady]} {
            set jsonReady [expr {![catch {package require json}]}]
        }
        return $jsonReady
    }

    proc json_decode {text} {
        variable maxEnvelope
        if {![json_available]} {
            fail capability_not_available "Tcl json package is unavailable"
        }
        if {[string bytelength $text] > $maxEnvelope} {
            fail result_too_large "JSON envelope exceeds its byte limit"
        }
        if {[catch {json::json2dict $text} value]} {
            fail invalid_arguments "Invalid JSON envelope"
        }
        if {![string is list $value]} {
            fail invalid_arguments "JSON envelope must be an object"
        }
        return $value
    }

    proc json_quote {value} {
        set output "\""
        set length [string length $value]
        for {set index 0} {$index < $length} {incr index} {
            set char [string index $value $index]
            set code [scan $char %c]
            if {$char eq "\\"} {
                append output "\\\\"
            } elseif {$char eq "\""} {
                append output "\\\""
            } elseif {$char eq "\n"} {
                append output "\\n"
            } elseif {$char eq "\r"} {
                append output "\\r"
            } elseif {$char eq "\t"} {
                append output "\\t"
            } elseif {$code < 32 || $code == 127} {
                append output [format "\\u%04x" $code]
            } else {
                append output $char
            }
        }
        append output "\""
        return $output
    }

    proc json_meta {{traceId ""} {source "tcl"} {target "python"} {deadlineMs 5000}} {
        variable maxId
        variable maxDeadline
        if {[string length $traceId] > $maxId} {
            fail invalid_arguments "DataHub trace id exceeds its limit"
        }
        if {![string is integer -strict $deadlineMs] || $deadlineMs <= 0 || $deadlineMs > $maxDeadline} {
            fail invalid_arguments "DataHub deadline is invalid"
        }
        return [format {{"trace_id":%s,"source":%s,"target":%s,"deadline_ms":%d}} \
            [json_quote $traceId] [json_quote $source] [json_quote $target] $deadlineMs]
    }

    proc json_page {offset limit total next truncated} {
        variable maxEnvelope
        foreach {name value} [list offset $offset limit $limit total $total] {
            if {![string is integer -strict $value] || $value < 0} {
                fail invalid_arguments "DataHub page $name is invalid"
            }
        }
        if {$limit < 1} {
            fail invalid_arguments "DataHub page bounds are invalid"
        }
        set truncatedValue [expr {$truncated ? 1 : 0}]
        if {$truncatedValue} {
            if {![string is integer -strict $next] || $next <= $offset || $next > $total} {
                fail invalid_arguments "DataHub page next offset is invalid"
            }
            set nextJson $next
        } else {
            if {$next ne ""} {
                fail invalid_arguments "DataHub page next offset is invalid"
            }
            set nextJson "null"
        }
        set text [format {{"offset":%d,"limit":%d,"total":%d,"next":%s,"truncated":%s}} \
            $offset $limit $total $nextJson [compact_truncated $truncated]]
        if {[string bytelength $text] > $maxEnvelope} {
            fail result_too_large "DataHub page exceeds its byte limit"
        }
        return $text
    }

    proc compact_truncated {value} {
        if {$value} {return true}
        return false
    }

    proc json_error {code message {detailsJson "{}"}} {
        variable maxText
        if {[lsearch -exact $::datahubErrorCodes $code] < 0} {
            fail invalid_arguments "Unknown DataHub error code"
        }
        if {$message eq ""} {
            fail invalid_arguments "DataHub error message must not be empty"
        }
        if {[string bytelength $message] > $maxText} {
            fail result_too_large "DataHub error message exceeds its limit"
        }
        return [format {{"code":%s,"message":%s,"details":%s}} \
            [json_quote $code] [json_quote $message] $detailsJson]
    }

    proc json_envelope {contract version requestId action status payloadJson
                        {pageJson ""} {errorJson ""} {metaJson ""}} {
        variable maxEnvelope
        variable maxPayload
        if {[lsearch -exact {ok error unknown} $status] < 0} {
            fail invalid_arguments "Invalid DataHub status"
        }
        if {![string is integer -strict $version] || $version < 1} {
            fail invalid_arguments "Invalid DataHub contract version"
        }
        if {$payloadJson eq ""} {set payloadJson "{}"}
        if {[string bytelength $payloadJson] > $maxPayload} {
            fail result_too_large "DataHub payload exceeds its byte limit"
        }
        if {$metaJson eq ""} {set metaJson [json_meta]}
        if {$status eq "error" && $errorJson eq ""} {
            fail invalid_arguments "Failed envelope requires an error"
        }
        if {$status ne "error" && $errorJson eq ""} {
            set errorJson "null"
        }
        set fields [list \
            [format {"hub":"sico.datahub.v1","contract":%s,"version":%d} \
                [json_quote $contract] $version] \
            [format {"id":%s,"action":%s,"status":%s} \
                [json_quote $requestId] [json_quote $action] [json_quote $status]] \
            [format {"payload":%s} $payloadJson] \
            [format {"meta":%s} $metaJson] \
            [format {"error":%s} $errorJson]]
        if {$pageJson ne ""} {
            set fields [linsert $fields 2 [format {"page":%s} $pageJson]]
        }
        set text "{[join $fields ,]}"
        if {[string bytelength $text] > $maxEnvelope} {
            fail result_too_large "DataHub envelope exceeds its byte limit"
        }
        return $text
    }

    proc contract {name {version ""} {action ""}} {
        set found ""
        foreach row $::datahubContractRows {
            if {[lindex $row 0] eq $name} {
                set found $row
                break
            }
        }
        if {$found eq ""} {
            fail unsupported_contract "Unsupported DataHub contract: $name"
        }
        if {$version ne "" && [lindex $found 1] != $version} {
            fail version_mismatch "DataHub contract version is not supported"
        }
        if {$action ne ""} {
            foreach spec [lindex $found 2] {
                if {[lindex $spec 0] eq $action} {
                    return $spec
                }
            }
            fail unsupported_contract "Unsupported DataHub contract action: $action"
        }
        return $found
    }

    proc check_request {name version action payloadJson} {
        variable maxText
        if {![json_available]} {
            fail capability_not_available "Request validation requires Tcl json"
        }
        set spec [contract $name $version $action]
        set fields [lindex $spec 3]
        set payload [json_decode $payloadJson]
        set names [dict keys $payload]
        if {[llength $names] != [llength $fields] || [lsort $names] ne [lsort $fields]} {
            fail invalid_arguments "DataHub request fields do not match the contract"
        }
        if {[dict exists $payload limit]} {
            set limit [dict get $payload limit]
            set maxItems [lindex [lindex [contract $name $version] 3] 0]
            if {![string is integer -strict $limit] || $limit < 1 || $limit > $maxItems} {
                fail invalid_arguments "DataHub request limit is invalid"
            }
        }
        if {[dict exists $payload offset]} {
            set offset [dict get $payload offset]
            if {![string is integer -strict $offset] || $offset < 0 || $offset > 4096} {
                fail invalid_arguments "DataHub request offset is invalid"
            }
        }
        if {[dict exists $payload query]} {
            if {[string bytelength [dict get $payload query]] > $maxText} {
                fail result_too_large "DataHub request query exceeds its limit"
            }
        }
        return $spec
    }

    proc valid_token {value} {
        return [regexp {^[A-Za-z0-9_.-]{1,128}$} $value]
    }

    proc dh1_encode {contract version requestId action payloadJson} {
        variable maxEnvelope
        variable maxPayload
        if {![valid_token $contract] || ![valid_token $requestId] || ![valid_token $action]} {
            fail invalid_arguments "Invalid DH1 scalar field"
        }
        if {![string is integer -strict $version] || $version < 1} {
            fail invalid_arguments "Invalid DH1 version"
        }
        if {[string bytelength $payloadJson] > $maxPayload} {
            fail result_too_large "DH1 payload exceeds its byte limit"
        }
        if {[string index $payloadJson 0] ne "{" || [string index $payloadJson end] ne "}"} {
            fail invalid_arguments "DH1 payload must be a JSON object"
        }
        if {[json_available] && [catch {json_decode $payloadJson}]} {
            fail invalid_arguments "DH1 payload is invalid JSON"
        }
        set encoded [binary encode base64 -maxlen 0 [encoding convertto utf-8 $payloadJson]]
        set line "DH1 $contract $version $requestId $action $encoded"
        if {[string bytelength $line] > $maxEnvelope} {
            fail result_too_large "DH1 line exceeds its byte limit"
        }
        return $line
    }

    proc dh1_decode {line} {
        variable maxEnvelope
        variable maxPayload
        if {[string bytelength $line] > $maxEnvelope} {
            fail result_too_large "DH1 line exceeds its byte limit"
        }
        set fields [split $line " "]
        if {[llength $fields] != 6 || [lindex $fields 0] ne "DH1"} {
            fail invalid_arguments "Invalid DH1 frame"
        }
        lassign $fields marker contract version requestId action encoded
        if {![valid_token $contract] || ![valid_token $requestId] || ![valid_token $action]} {
            fail invalid_arguments "Invalid DH1 scalar field"
        }
        if {![string is integer -strict $version] || $version < 1} {
            fail invalid_arguments "Invalid DH1 version"
        }
        if {[catch {binary decode base64 $encoded} payloadBytes]} {
            fail invalid_arguments "Invalid DH1 payload encoding"
        }
        if {[string bytelength $payloadBytes] > $maxPayload} {
            fail result_too_large "DH1 payload exceeds its byte limit"
        }
        if {[catch {encoding convertfrom utf-8 $payloadBytes} payloadJson]} {
            fail invalid_arguments "DH1 payload is not UTF-8"
        }
        if {[json_available] && [catch {json_decode $payloadJson}]} {
            fail invalid_arguments "DH1 payload is invalid JSON"
        }
        return [dict create contract $contract version $version \
            id $requestId action $action payload $payloadJson]
    }

    proc send_line {channel line} {
        variable maxEnvelope
        if {[string first "\n" $line] >= 0 || [string first "\r" $line] >= 0} {
            fail invalid_arguments "Line frame contains multiple records"
        }
        if {[string bytelength $line] > $maxEnvelope} {
            fail result_too_large "Line frame exceeds its byte limit"
        }
        fconfigure $channel -translation lf -buffering line -encoding utf-8
        puts $channel $line
        flush $channel
    }

    proc receive_line {channel {timeoutMs -1}} {
        variable maxEnvelope
        variable readCounter
        incr readCounter
        set oldBlocking [fconfigure $channel -blocking]
        fconfigure $channel -blocking 0 -translation lf -encoding utf-8
        set deadline ""
        if {$timeoutMs >= 0} {
            set deadline [expr {[clock milliseconds] + $timeoutMs}]
        }
        try {
            while {1} {
                set count [gets $channel line]
                if {$count >= 0} {
                    break
                }
                if {[eof $channel]} {
                    fail read_failed "DataHub channel closed before a line arrived"
                }
                if {$deadline ne "" && [clock milliseconds] >= $deadline} {
                    fail timeout "DataHub line receive timed out"
                }
                after 5
            }
        } finally {
            catch {fconfigure $channel -blocking $oldBlocking}
        }
        if {[string bytelength $line] > $maxEnvelope} {
            fail result_too_large "Line frame exceeds its byte limit"
        }
        return $line
    }

    proc connect {host port} {
        set channel [socket $host $port]
        fconfigure $channel -translation lf -buffering line -blocking 1 -encoding utf-8
        return $channel
    }

    proc endpoint_hello {channel token sessionRef} {
        if {![json_available]} {
            fail capability_not_available "Endpoint hello requires Tcl json"
        }
        set payload [format {{"token":%s,"session_ref":%s}} \
            [json_quote $token] [json_quote $sessionRef]]
        set request [json_envelope \
            sico.datahub.hello.v1 1 tcl-hello hello ok $payload]
        send_line $channel $request
        set reply [json_decode [receive_line $channel 5000]]
        if {[dict get $reply contract] ne "sico.datahub.hello.v1"
            || [dict get $reply action] ne "hello"
            || [dict get $reply id] ne "tcl-hello"} {
            fail invalid_arguments "Endpoint hello identity mismatch"
        }
        if {[dict get $reply status] ne "ok"} {
            set error [dict get $reply error]
            fail [dict get $error code] [dict get $error message]
        }
        if {[dict get [dict get $reply payload] schema] ne "datahub.hello.v1"} {
            fail invalid_arguments "Endpoint hello schema mismatch"
        }
        return $reply
    }

    proc endpoint_exchange {channel contract version requestId action payloadJson} {
        if {![json_available]} {
            fail capability_not_available "Endpoint exchange requires Tcl json"
        }
        set request [json_envelope $contract $version $requestId $action \
            ok $payloadJson]
        send_line $channel $request
        set reply [json_decode [receive_line $channel 5000]]
        if {[dict get $reply contract] ne $contract
            || [dict get $reply version] != $version
            || [dict get $reply action] ne $action
            || [dict get $reply id] ne $requestId} {
            fail invalid_arguments "Endpoint response identity mismatch"
        }
        return $reply
    }

    proc close {channel} {
        catch {close $channel}
    }
}
