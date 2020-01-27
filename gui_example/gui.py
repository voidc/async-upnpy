from gi.repository import Gtk
import gi
gi.require_version('Gtk', '3.0')

textview = None


def on_selection_changed(sel):
    model, treeiter = sel.get_selected()
    if treeiter is not None:
        textview.get_buffer().set_text(model[treeiter][0])
        print("You selected", model[treeiter][0])


def main():
    win = Gtk.Window()
    win.connect("destroy", Gtk.main_quit)
    win.set_border_width(8)

    hbox = Gtk.Box(spacing=4)
    win.add(hbox)

    frame1 = Gtk.Frame()
    hbox.pack_start(frame1, False, False, 0)

    store = Gtk.ListStore(str)
    store.append(["Some Device 1"])
    store.append(["Some Device 2"])

    tree = Gtk.TreeView(store)
    renderer = Gtk.CellRendererText()
    column = Gtk.TreeViewColumn("Devices", renderer, text=0)
    tree.append_column(column)

    select = tree.get_selection()
    select.connect("changed", on_selection_changed)

    frame1.add(tree)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    hbox.pack_start(vbox, True, True, 0)

    image = Gtk.Image()
    vbox.pack_start(image, False, False, 0)

    frame2 = Gtk.Frame()
    vbox.pack_start(frame2, True, True, 0)

    scrolledwindow = Gtk.ScrolledWindow()
    frame2.add(scrolledwindow)

    global textview
    textview = Gtk.TextView()
    textview.set_editable(False)
    textview.set_cursor_visible(False)
    scrolledwindow.add(textview)

    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
