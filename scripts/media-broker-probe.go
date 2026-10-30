// Software-only RTSP relay/ACL probe. It never opens a camera or GPIO device.
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/pion/rtp"
)

func main() {
	address := flag.String("url", "rtsp://127.0.0.1:18554/front", "isolated test broker only")
	count := flag.Int("packets", 120, "number of synthetic RTP packets")
	deniedIP := flag.String("denied-source", "127.0.0.2", "loopback source to verify ACL denial")
	flag.Parse()
	if *count < 30 || *count > 600 {
		panic("packet count outside bounded test")
	}
	u, err := base.ParseURL(*address)
	must(err)
	host, _, err := net.SplitHostPort(u.Host)
	must(err)
	if !net.ParseIP(host).IsLoopback() {
		panic("probe requires an isolated loopback listener")
	}
	tcp := gortsplib.ProtocolTCP
	makeClient := func(source string) *gortsplib.Client {
		c := &gortsplib.Client{Scheme: u.Scheme, Host: u.Host, Protocol: &tcp, ReadTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second}
		if source != "" {
			c.DialContext = (&net.Dialer{LocalAddr: &net.TCPAddr{IP: net.ParseIP(source)}, Timeout: 3 * time.Second}).DialContext
		}
		return c
	}
	sps, _ := base64.StdEncoding.DecodeString("Z0IAH5WoFAFuQA==")
	pps, _ := base64.StdEncoding.DecodeString("aM4xUg==")
	f := &format.H264{PayloadTyp: 96, PacketizationMode: 1, SPS: sps, PPS: pps}
	media := &description.Media{Type: description.MediaTypeVideo, Formats: []format.Format{f}}
	session := &description.Session{Medias: []*description.Media{media}}
	publisher := makeClient("")
	must(publisher.StartRecording(*address, session))
	defer publisher.Close()
	// Verify an untrusted source cannot publish or read. These probes target only
	// the synthetic test path and cannot reach the production camera broker.
	bad := makeClient(*deniedIP)
	err = bad.StartRecording(*address, session)
	bad.Close()
	publishDenied := err != nil && strings.Contains(err.Error(), "401")
	bad = makeClient(*deniedIP)
	must(bad.Start())
	_, _, err = bad.Describe(u)
	bad.Close()
	readDenied := err != nil && strings.Contains(err.Error(), "401")
	if !publishDenied || !readDenied {
		panic("ACL did not reject the untrusted loopback peer")
	}
	reader := makeClient("")
	must(reader.Start())
	defer reader.Close()
	desc, _, err := reader.Describe(u)
	must(err)
	var receivedFormat *format.H264
	receivedMedia := desc.FindFormat(&receivedFormat)
	if receivedMedia == nil {
		panic("relay did not preserve H264")
	}
	_, err = reader.Setup(desc.BaseURL, receivedMedia, 0, 0)
	must(err)
	var lock sync.Mutex
	sent := map[uint32]time.Time{}
	var delays []float64
	corrupt := 0
	marker := []byte("TM1relayprobe-v1")
	reader.OnPacketRTP(receivedMedia, receivedFormat, func(packet *rtp.Packet) {
		payload := packet.Payload
		if len(payload) != 28 || payload[0] != 6 || !bytes.Equal(payload[3:19], marker) {
			return
		}
		n, err := strconv.ParseUint(string(payload[19:27]), 16, 32)
		if err != nil {
			return
		}
		id := uint32(n)
		lock.Lock()
		defer lock.Unlock()
		if start, ok := sent[id]; ok {
			if packet.Timestamp != id*3000 {
				corrupt++
			}
			delays = append(delays, float64(time.Since(start))/float64(time.Millisecond))
			delete(sent, id)
		}
	})
	_, err = reader.Play(nil)
	must(err)
	ticker := time.NewTicker(time.Second / 30)
	defer ticker.Stop()
	for i := uint32(1); i <= uint32(*count); i++ {
		<-ticker.C
		payload := append([]byte{6, 5, 24}, marker...)
		payload = append(payload, []byte(fmt.Sprintf("%08x", i))...)
		payload = append(payload, 0x80)
		packet := &rtp.Packet{Header: rtp.Header{Version: 2, Marker: true, PayloadType: 96, SequenceNumber: uint16(i), Timestamp: i * 3000, SSRC: 0x544d3101}, Payload: payload}
		lock.Lock()
		sent[i] = time.Now()
		lock.Unlock()
		must(publisher.WritePacketRTP(media, packet))
	}
	time.Sleep(250 * time.Millisecond)
	lock.Lock()
	defer lock.Unlock()
	sort.Float64s(delays)
	if len(delays) < *count*95/100 || corrupt != 0 {
		panic(fmt.Sprintf("relay failed received=%d corrupt=%d", len(delays), corrupt))
	}
	p95 := delays[(len(delays)-1)*95/100]
	json.NewEncoder(os.Stdout).Encode(map[string]any{"scope": "synthetic_loopback_RTSP_relay_not_glass_to_glass_camera_latency", "sent": *count, "received": len(delays), "timestamp_corruption": corrupt, "p50_ms": delays[len(delays)/2], "p95_ms": p95, "max_ms": delays[len(delays)-1], "untrusted_publish_denied": publishDenied, "untrusted_read_denied": readDenied})
	if p95 > 100 {
		panic("relay p95 exceeds conservative 100ms acceptance ceiling")
	}
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}

// Keep the compile-time DialContext contract explicit across gortsplib updates.
var _ func(context.Context, string, string) (net.Conn, error) = (&net.Dialer{}).DialContext
