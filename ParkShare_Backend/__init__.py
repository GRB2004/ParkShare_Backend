import pymysql

pymysql.install_as_MySQLdb()

# Django 6.1 requiere por defecto MySQL >= 8.4.
# Este parche habilita compatibilidad completa con MySQL 8.0.x (como MySQL 8.0.46 instalado en el sistema).
from django.db.backends.mysql.features import DatabaseFeatures
DatabaseFeatures.minimum_database_version = property(
    lambda self: (10, 11) if self.connection.mysql_is_mariadb else (8, 0)
)
