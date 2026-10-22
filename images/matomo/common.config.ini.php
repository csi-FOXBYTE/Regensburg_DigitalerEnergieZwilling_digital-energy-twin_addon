; <?php exit; ?> DO NOT REMOVE THIS LINE
; Merged below the config.ini.php the installer writes. The entrypoint appends
; environment-specific sections on every start.

[Tracker]
; Required for the DEZ tracker: a visit is the tab session sent as _id.
; By default Matomo merges everyone with the same IP and browser into one visit.
trust_visitors_cookies = 1

[log]
; Matomo's own warnings and errors go to the Apache error log, i.e. the
; container's stderr. The default writer "screen" only prints into responses.
log_writers[] = errorlog
log_level = WARN
