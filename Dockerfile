FROM ontop/ontop:5.5.0

# Official Ontop classpath includes /opt/ontop/jdbc/*; keep its entrypoint,
# unprivileged user and healthcheck. No host Java/Maven installation needed.
# SHA-256 of the MySQL Connector/J 8.4.0 artifact from Maven Central.
ADD --chmod=644 --checksum=sha256:d77962877d010777cff997015da90ee689f0f4bb76848340e1488f2b83332af5 https://repo.maven.apache.org/maven2/com/mysql/mysql-connector-j/8.4.0/mysql-connector-j-8.4.0.jar /opt/ontop/jdbc/mysql-connector-j-8.4.0.jar

# ADD can apply 0644 to newly created parent directories too. The runtime
# user needs directory traversal (0755), while the JAR stays read-only (0644).
USER root
RUN chmod 755 /opt/ontop/jdbc
USER ontop
