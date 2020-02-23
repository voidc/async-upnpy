CC=gcc

GTK3_CFLAGS=$(shell pkg-config --cflags gtk+-3.0)
GTK3_LIBS=$(shell pkg-config --libs gtk+-3.0)

CFLAGS=-I.
LIBS=

all: gui

gui: gui.c
	$(CC) $(CFLAGS) $(GTK3_CFLAGS) -o gui gui.c $(LIBS) $(GTK3_LIBS)

clean:
	rm -f gui