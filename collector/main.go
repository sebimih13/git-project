// A finite-capture receiver host. The actual Trace2 parsing and OTLP conversion
// come from the sibling trace2receiver checkout, without modifying its sources.
package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"os"
	"sync"

	tr "github.com/git-ecosystem/trace2receiver"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.uber.org/zap"
)

func main() {
	socket := flag.String("socket", "", "fresh Unix socket path")
	output := flag.String("output", "", "OTLP JSONL output path")
	validate := flag.String("validate-otlp", "", "validate a completed OTLP JSON document and exit")
	flag.Parse()
	if *validate != "" {
		data, err := os.ReadFile(*validate)
		if err != nil {
			panic(err)
		}
		traces, err := (&ptrace.JSONUnmarshaler{}).UnmarshalTraces(data)
		if err != nil {
			panic(err)
		}
		fmt.Printf("Valid OTLP JSON: %d spans\n", traces.SpanCount())
		return
	}
	if *socket == "" || *output == "" {
		panic("--socket and --output required")
	}
	// The receiver unlinks existing sockets: refuse to take over any path.
	if _, err := os.Lstat(*socket); !os.IsNotExist(err) {
		panic("socket path already exists or cannot be checked")
	}
	file, err := os.OpenFile(*output, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		panic(err)
	}
	defer file.Close()
	var mutex sync.Mutex
	next, err := consumer.NewTraces(func(_ context.Context, traces ptrace.Traces) error {
		data, err := (&ptrace.JSONMarshaler{}).MarshalTraces(traces)
		if err != nil {
			return err
		}
		mutex.Lock()
		defer mutex.Unlock()
		_, err = file.Write(append(data, '\n'))
		return err
	})
	if err != nil {
		panic(err)
	}
	rcvr := &tr.Rcvr_UnixSocket{SocketPath: *socket, Base: &tr.Rcvr_Base{
		Logger: zap.NewNop(), TracesConsumer: next, RcvrConfig: &tr.Config{
			Filter: &tr.FilterSettings{Defaults: tr.FilterDefaults{RulesetName: tr.DetailLevelVerboseName}},
		},
	}}
	ctx := context.Background()
	if err := rcvr.Start(ctx, nil); err != nil {
		panic(err)
	}
	fmt.Println("READY")
	// Caller waits for the expected batches before signalling shutdown.
	bufio.NewReader(os.Stdin).ReadString('\n')
	if err := rcvr.Shutdown(ctx); err != nil {
		panic(err)
	}
}
