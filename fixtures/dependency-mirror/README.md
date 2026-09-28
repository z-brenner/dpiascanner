# dependency-mirror

A local package mirror laid out as `<ecosystem>/<name>-<version>/`, used by
`lantern_registry.resolver.LocalDirectorySource` so dependency profiling tests run offline.
The packages are fictional. `acme-geo` sends coordinates to `api.acme-geo.example`;
`tiny-utils` makes no network calls.
