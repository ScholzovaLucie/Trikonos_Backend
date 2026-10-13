from rest_framework.pagination import PageNumberPagination


class StorefrontPagination(PageNumberPagination):
    page_size = 24
    page_size_query_param = 'pageSize'
    max_page_size = 100


class AdminPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'pageSize'
    max_page_size = 100
