#!/usr/bin/env bash
#
# Add the public cluster to ~/.kube/config as context `visitdoc-public`.
#
# Usage: scripts/public-kubeconfig.sh <server-address>      (make k8s-public-context)
#
# Reads the kubeconfig k3s wrote on the server, renames its `default` cluster, user and
# context to `visitdoc-public`, and merges it into ~/.kube/config. Its address is left at
# https://127.0.0.1:6443: the server's API is closed to the internet, and that is the local
# end of the SSH tunnel to it (make k8s-tunnel). Any earlier `visitdoc-public` entries are
# replaced - k3s issues new credentials when it is reinstalled - and the current context is
# kept. The previous file is saved as ~/.kube/config.bak.
#
# The file it reads holds the cluster's admin credentials, so it is written only to a
# private temporary file, removed on exit, and never printed.
set -euo pipefail

host="${1:?usage: $0 <server-address>}"
name=visitdoc-public
config="$HOME/.kube/config"

tmp="$(mktemp)"
trap 'rm -f "$tmp" "$tmp.merged"' EXIT
chmod 600 "$tmp"

ssh "root@$host" cat /etc/rancher/k3s/k3s.yaml \
  | sed -E "s/^(\s*-?\s*(name|cluster|user|current-context): )default$/\1$name/" > "$tmp"
if grep -qE ': default$' "$tmp" || ! grep -q "server: https://127.0.0.1:6443" "$tmp"; then
  echo "the server's kubeconfig is not in the shape expected; ~/.kube/config left as it was" >&2
  exit 1
fi

mkdir -p "$(dirname "$config")"
touch "$config"
cp "$config" "$config.bak"
for kind in context cluster user; do
  kubectl config "delete-$kind" "$name" >/dev/null 2>&1 || true
done
# The first file named wins where both set a value, so the current context stays this one's.
KUBECONFIG="$config:$tmp" kubectl config view --flatten > "$tmp.merged"
mv "$tmp.merged" "$config"
chmod 600 "$config"
echo "context $name added to $config (previous file: $config.bak)"
