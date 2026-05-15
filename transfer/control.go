// dragdrop_ctrl.go
// Controller for dragdrop_window.exe via named pipe \\.\pipe\DragDropCtrl
//
// On first run the controller launches dragdrop_window.exe automatically
// (expected to sit next to this binary, or set DRAGDROP_WINDOW_EXE env var).
//
// Usage:
//   dragdrop_ctrl.exe move <x> <y>
//   dragdrop_ctrl.exe file <path>
//   dragdrop_ctrl.exe quit
//   dragdrop_ctrl.exe interactive        <- REPL mode

package transfer

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const pipeName = `\\.\pipe\DragDropCtrl`

// Windows error codes
const (
	errPipeBusy     = syscall.Errno(231) // ERROR_PIPE_BUSY
	detachedProcess = 0x00000008         // DETACHED_PROCESS
)

var (
	modkernel32       = syscall.NewLazyDLL("kernel32.dll")
	procWaitNamedPipe = modkernel32.NewProc("WaitNamedPipeW")
)

func waitNamedPipe(name string, timeoutMs uint32) error {
	p, err := syscall.UTF16PtrFromString(name)
	if err != nil {
		return err
	}
	r, _, e := procWaitNamedPipe.Call(uintptr(unsafe.Pointer(p)), uintptr(timeoutMs))
	if r == 0 {
		return e
	}
	return nil
}

// windowExePath returns the path to dragdrop_window.exe.
// Checks DRAGDROP_WINDOW_EXE env var first, then looks next to this binary.
func windowExePath() string {
	self, err := os.Executable()
	if err != nil {
		return "dragdrop.exe"
	}
	return filepath.Join(filepath.Dir(self), "dragdrop.exe")
}

// tryOpenPipe attempts a single non-blocking open of the named pipe.
func tryOpenPipe() (syscall.Handle, bool) {
	p, _ := syscall.UTF16PtrFromString(pipeName)
	h, err := syscall.CreateFile(p, syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_EXISTING, 0, 0)
	if err == nil {
		return h, true
	}
	return syscall.InvalidHandle, false
}

// ensureWindowRunning launches dragdrop_window.exe if the pipe is not already available.
func ensureWindowRunning() {
	// Pipe already exists -> window is running, nothing to do.
	if h, ok := tryOpenPipe(); ok {
		syscall.CloseHandle(h)
		return
	}

	exePath := windowExePath()
	exeW, err := syscall.UTF16PtrFromString(exePath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Bad exe path:", err)
		return
	}

	si := &syscall.StartupInfo{}
	pi := &syscall.ProcessInformation{}
	err = syscall.CreateProcess(
		exeW,  // application name
		nil,   // command line
		nil,   // process security attrs
		nil,   // thread security attrs
		false, // inherit handles
		syscall.CREATE_NEW_PROCESS_GROUP|detachedProcess,
		nil, // environment
		nil, // working directory
		si,
		pi,
	)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to launch %s: %v\n", exePath, err)
		return
	}
	syscall.CloseHandle(pi.Thread)
	syscall.CloseHandle(pi.Process)

	fmt.Printf("Launched %s, waiting for pipe...\n", exePath)

	// Poll until the pipe is ready (up to ~3 s)
	for i := 0; i < 15; i++ {
		time.Sleep(200 * time.Millisecond)
		if h, ok := tryOpenPipe(); ok {
			syscall.CloseHandle(h)
			fmt.Println("Window ready.")
			return
		}
	}
	fmt.Fprintln(os.Stderr, "Warning: window launched but pipe not ready after 3 s")
}

// openPipe dials the named pipe with retries.
func openPipe() (*os.File, error) {
	for retries := 0; retries < 10; retries++ {
		p, err := syscall.UTF16PtrFromString(pipeName)
		if err != nil {
			return nil, err
		}
		h, err := syscall.CreateFile(p, syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_EXISTING, 0, 0)
		if err == nil {
			return os.NewFile(uintptr(h), pipeName), nil
		}
		if err == errPipeBusy {
			_ = waitNamedPipe(pipeName, 2000)
			continue
		}
		time.Sleep(200 * time.Millisecond)
	}
	return nil, fmt.Errorf("could not open pipe %s (is dragdrop_window.exe running?)", pipeName)
}

// sendCommand opens the pipe, writes one command line, then closes.
func sendCommand(cmd string) error {
	f, err := openPipe()
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.WriteString(cmd + "\n")
	return err
}

func usage() {
	fmt.Println(`dragdrop_ctrl - launch & control dragdrop_window.exe

dragdrop_window.exe is started automatically if not already running.
Place it next to this binary, or set DRAGDROP_WINDOW_EXE=<full path>.

Commands:
  move <x> <y>      Move the window to screen position x,y
  file <path>       Change the drag-drop file
  quit              Close the window
  interactive       Interactive REPL (or pipe commands to stdin)

Examples:
  dragdrop_ctrl.exe move 300 400
  dragdrop_ctrl.exe file "C:\samples\kick.wav"
  dragdrop_ctrl.exe quit
  dragdrop_ctrl.exe interactive`)
}

func runInteractive() {
	fmt.Println("Interactive mode. Commands: move x y | file <path> | quit | exit")

	scanner := bufio.NewScanner(os.Stdin)
	for {
		fmt.Print("> ")
		if !scanner.Scan() {
			break
		}
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if line == "exit" {
			break
		}
		if err := sendCommand(line); err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
		}
		if strings.ToUpper(line) == "QUIT" {
			break
		}
	}
}

func main() {
	args := os.Args[1:]

	if len(args) == 0 {
		usage()
		os.Exit(1)
	}

	// Always ensure the window is running before sending commands.
	ensureWindowRunning()

	verb := strings.ToLower(args[0])

	switch verb {

	case "move":
		if len(args) != 3 {
			fmt.Fprintln(os.Stderr, "Usage: dragdrop_ctrl.exe move <x> <y>")
			os.Exit(1)
		}
		x, err1 := strconv.Atoi(args[1])
		y, err2 := strconv.Atoi(args[2])
		if err1 != nil || err2 != nil {
			fmt.Fprintln(os.Stderr, "x and y must be integers")
			os.Exit(1)
		}
		cmd := fmt.Sprintf("MOVE %d %d", x, y)
		if err := sendCommand(cmd); err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			os.Exit(1)
		}
		fmt.Println("Sent:", cmd)

	case "file":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "Usage: dragdrop_ctrl.exe file <path>")
			os.Exit(1)
		}
		path := strings.Join(args[1:], " ")
		cmd := "FILE " + path
		if err := sendCommand(cmd); err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			os.Exit(1)
		}
		fmt.Println("Sent:", cmd)

	case "quit":
		if err := sendCommand("QUIT"); err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			os.Exit(1)
		}
		fmt.Println("Sent: QUIT")

	case "interactive":
		runInteractive()

	default:
		fmt.Fprintf(os.Stderr, "Unknown command %q\n\n", verb)
		usage()
		os.Exit(1)
	}
}
