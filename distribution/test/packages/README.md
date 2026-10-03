# Independent CI dependencies

The wheel in this directory is the independently built Microsoft MIT-licensed
publishing library. Its source revision, SHA-256 and exact verified Python files
are recorded in `publishing.json`. CI verifies the digest before installation.

The publishing repository is currently private, and a repository-scoped Actions
token cannot read sibling repositories. Carrying the already qualified public
package artifact lets the integration fixture test the real library without
copying its source into another component or requiring a developer credential.
This directory is excluded from the distribution npm release. It is a fixture
receipt, not a deployment dependency policy; deployments resolve current sources.
Update the wheel and its provenance together after independent qualification.
