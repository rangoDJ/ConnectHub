# Added by ConnectHub: the log pipe to svc-selkies-log only carries stdout, and Selkies
# logs to stderr
exec 2>&1
# Added by ConnectHub: Selkies logs clipboard transfers at debug level
if [ "${CLIPBOARD_DEBUG,,}" = "true" ]; then
  export SELKIES_DEBUG=true
fi
