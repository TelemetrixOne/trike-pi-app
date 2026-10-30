// Package upgrade keeps vendor changes within the reviewed Git deployment path.
// Original TelemetrixOne patch: Apache-2.0. MediaMTX remains under its MIT licence.
package upgrade

import "fmt"

// Upgrade must not replace an audited binary with an uncommitted download.
func Upgrade(_, _ string) error {
	return fmt.Errorf("self-update disabled in Git-managed TelemetrixOne builds")
}

// CheckVersion is deliberately offline; vendor updates are reviewed in Git.
func CheckVersion(_, _ string) (bool, error) {
	return false, fmt.Errorf("self-update checks disabled in Git-managed TelemetrixOne builds")
}
