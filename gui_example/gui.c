#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <glib.h>
#include <glib/gprintf.h>
#include <glib/gi18n.h>
#include <gtk/gtk.h>
#include <gdk-pixbuf/gdk-pixbuf.h>

static GtkWidget *window;
static GtkTreeStore *device_store;
static GtkWidget *details_view;
static GtkWidget *image;

static GIOChannel *channel;
static gboolean verbose = FALSE;

enum read_state {
    READ_DEVICE,
    READ_META,
    READ_ICON,
};

void on_selection_changed(GtkWidget *widget, GtkWidget *tree_view) {
    GtkTreeIter iter;
    GtkTreeModel *model;
    gchar *usn;
    GtkListStore *details_store;
    GdkPixbuf *pixbuf;

    if (gtk_tree_selection_get_selected(GTK_TREE_SELECTION(widget), &model, &iter)) {
        gtk_tree_model_get(model, &iter, 0, &usn, 1, &details_store, 2, &pixbuf, -1);
        //printf("selected: %s\n", usn);
        gtk_tree_view_set_model(GTK_TREE_VIEW(details_view), GTK_TREE_MODEL(details_store));
        gtk_image_set_from_pixbuf(GTK_IMAGE(image), pixbuf);
        g_free(usn);
    }
}

GdkPixbuf *read_image(gchar *base64_data) {
    gsize len;
    guchar *icon_data;
    GdkPixbufLoader *loader;
    GdkPixbuf *pixbuf;

    icon_data = g_base64_decode(base64_data, &len);
    loader = gdk_pixbuf_loader_new_with_type("png", NULL);
    gdk_pixbuf_loader_write(loader, icon_data, len, NULL);
    gdk_pixbuf_loader_close(loader, NULL);
    pixbuf = gdk_pixbuf_loader_get_pixbuf(loader);

    g_free(icon_data);
    return pixbuf;
}

gboolean network_read(GIOChannel *source, GIOCondition cond, gpointer data) {
    GString *line = g_string_new(NULL);
    GError *error = NULL;
    GIOStatus ret;

    static GtkTreeIter device_iter, parent_iter;
    static GtkListStore *details_store;
    static enum read_state state = READ_DEVICE;

    GtkTreeIter details_iter;
    gchar *pos = NULL;
    GdkPixbuf *pixbuf;

    ret = g_io_channel_read_line_string(source, line, NULL, &error);
    if (ret == G_IO_STATUS_ERROR || ret == G_IO_STATUS_EOF || line->len == 0) {
        return TRUE;
    }

    g_string_truncate(line, line->len - 1); // remove \n
    if (verbose)
        printf("> %s\n", line->str);

    if (strncmp(line->str, "DEVICE ", 7) == 0) {
        details_store = gtk_list_store_new(2, G_TYPE_STRING, G_TYPE_STRING);
        gtk_tree_store_append(device_store, &device_iter, NULL);
        gtk_tree_store_set(device_store, &device_iter, 0, line->str + 7, 1, details_store, 2, NULL, -1);
        parent_iter = device_iter;
    } else if (strncmp(line->str, "SUBDEVICE ", 10) == 0) {
        details_store = gtk_list_store_new(2, G_TYPE_STRING, G_TYPE_STRING);
        gtk_tree_store_append(device_store, &device_iter, &parent_iter);
        gtk_tree_store_set(device_store, &device_iter, 0, line->str + 10, 1, details_store, 2, NULL, -1);
    } else if (strncmp(line->str, "META ", 5) == 0) {
        state = READ_META;
    } else if (strncmp(line->str, "ICON ", 5) == 0) {
        state = READ_ICON;
    } else if (state == READ_META && (pos = strchr(line->str, ':')) != NULL) {
        *pos = '\0';
        gtk_list_store_append(details_store, &details_iter);
        gtk_list_store_set(details_store, &details_iter, 0, line->str, 1, pos + 1, -1);
    } else if (state == READ_ICON) {
        pixbuf = read_image(line->str);
        gtk_tree_store_set(device_store, &device_iter, 2, pixbuf, -1);
    }

    return TRUE;
}

static gboolean window_close(GtkWidget *widget, GdkEvent *event, gpointer data) {
    g_io_channel_shutdown(channel, FALSE, NULL);
    gtk_main_quit();
    return FALSE;
}

void create_window() {
    GtkWidget *paned;
    GtkWidget *vbox;
    GtkWidget *frame;
    GtkWidget *scrolled_window;
    GtkWidget *tree_view;
    GtkCellRenderer *renderer;
    GtkTreeViewColumn *column;
    GtkTreeSelection *selection;

    window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window), "UPnP Discover");
    gtk_window_set_default_size(GTK_WINDOW(window), 600, 400);
    gtk_container_set_border_width(GTK_CONTAINER(window), 8);
    g_signal_connect(G_OBJECT(window), "delete_event", G_CALLBACK(window_close), NULL);

    paned = gtk_paned_new(GTK_ORIENTATION_HORIZONTAL);
    gtk_paned_set_wide_handle(GTK_PANED(paned), TRUE);
    gtk_container_add(GTK_CONTAINER(window), paned);

    frame = gtk_frame_new(NULL);
    gtk_paned_pack1(GTK_PANED(paned), frame, FALSE, FALSE);
    scrolled_window = gtk_scrolled_window_new(NULL, NULL);
    gtk_widget_set_size_request(scrolled_window, 150, -1);
    gtk_container_add(GTK_CONTAINER(frame), scrolled_window);

    device_store = gtk_tree_store_new(3, G_TYPE_STRING, GTK_TYPE_LIST_STORE, GDK_TYPE_PIXBUF);
    tree_view = gtk_tree_view_new_with_model(GTK_TREE_MODEL(device_store));
    renderer = gtk_cell_renderer_text_new();
    column = gtk_tree_view_column_new_with_attributes("Device", renderer, "text", 0, NULL);
    gtk_tree_view_append_column(GTK_TREE_VIEW(tree_view), column);
    selection = gtk_tree_view_get_selection(GTK_TREE_VIEW(tree_view));
    g_signal_connect(selection, "changed", G_CALLBACK(on_selection_changed), tree_view);
    gtk_container_add(GTK_CONTAINER(scrolled_window), tree_view);

    vbox = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_paned_pack2(GTK_PANED(paned), vbox, TRUE, FALSE);

    image = gtk_image_new();
    gtk_box_pack_start(GTK_BOX(vbox), image, FALSE, FALSE, 0);
    gtk_image_set_from_pixbuf(GTK_IMAGE(image), NULL);

    frame = gtk_frame_new(NULL);
    gtk_box_pack_start(GTK_BOX(vbox), frame, TRUE, TRUE, 0);
    scrolled_window = gtk_scrolled_window_new(NULL, NULL);
    gtk_container_add(GTK_CONTAINER(frame), scrolled_window);

    details_view = gtk_tree_view_new();
    renderer = gtk_cell_renderer_text_new();
    column = gtk_tree_view_column_new_with_attributes("Property", renderer, "text", 0, NULL);
    gtk_tree_view_append_column(GTK_TREE_VIEW(details_view), column);
    column = gtk_tree_view_column_new_with_attributes("Value", renderer, "text", 1, NULL);
    gtk_tree_view_append_column(GTK_TREE_VIEW(details_view), column);
    gtk_container_add(GTK_CONTAINER(scrolled_window), details_view);
}

int init_channel(char *sock_path) {
    int sock;
    struct sockaddr_un addr;

    if ((sock = socket(AF_UNIX, SOCK_STREAM, 0)) < 0) {
        perror("socket");
        return -1;
    }

    memset(&addr, 0, sizeof(struct sockaddr_un));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, sock_path, sizeof(addr.sun_path) - 1);

    if (connect(sock, (const struct sockaddr *) &addr, sizeof(struct sockaddr_un)) < 0) {
        perror("connect");
        return -1;
    }

    channel = g_io_channel_unix_new(sock);
    g_io_add_watch(channel, G_IO_IN, (GIOFunc) network_read, NULL);
}

int main(int argc, char *argv[]) {
    char *sock_path = "/tmp/upnpy.sock";
    if (argc >= 2) {
        sock_path = argv[1];
    }

    gtk_init(&argc, &argv);
    create_window();
    gtk_widget_show_all(window);

    if (init_channel(sock_path) < 0) {
        return EXIT_FAILURE;
    }

    gtk_main();
    return 0;
}