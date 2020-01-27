#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    int sock;
    struct sockaddr_un addr;
    char buffer[1024];

    if (argc < 2)
        return 1;

    if ((sock = socket(AF_UNIX, SOCK_STREAM, 0)) < 0) {
        perror("socket");
        return 1;
    }

    memset(&addr, 0, sizeof(struct sockaddr_un));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, argv[1], sizeof(addr.sun_path) - 1);

    if (connect(sock, &addr, sizeof(struct sockaddr_un)) < 0) {
        perror("connect");
        return 1;
    }

    int n = 0;
    while (1) {
        if ((n = read(sock, buffer, 1024)) <= 0)
            break;
        buffer[n < 1024 ? n : 1024] = '\0';
        printf("%s\n", buffer);
    }

    close(sock);
    return 0;
}