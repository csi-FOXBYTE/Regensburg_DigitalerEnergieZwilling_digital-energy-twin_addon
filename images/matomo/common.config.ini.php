; <?php exit; ?> DO NOT REMOVE THIS LINE
; Merged below the config.ini.php the installer writes. The entrypoint appends
; environment-specific sections on every start.

[Tracker]
; Required for the DEZ tracker: a visit is the tab session sent as _id.
; By default Matomo merges everyone with the same IP and browser into one visit.
trust_visitors_cookies = 1
