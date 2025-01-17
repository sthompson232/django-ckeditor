from __future__ import absolute_import, unicode_literals

import inspect
import os
import warnings
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.html import escape
from django.utils.module_loading import import_string
from django.views import generic
from django.views.decorators.csrf import csrf_exempt

from ckeditor_uploader import utils
from ckeditor_uploader.backends import registry
from ckeditor_uploader.forms import SearchForm
from ckeditor_uploader.utils import storage

from .utils import is_valid_image_extension, is_valid_video_extension
import logging

logger = logging.getLogger(getattr(settings, 'CKEDITOR_LOGGER', 'django'))


def _get_user_path(user):
    user_path = ''

    # If CKEDITOR_RESTRICT_BY_USER is True upload file to user specific path.
    RESTRICT_BY_USER = getattr(settings, 'CKEDITOR_RESTRICT_BY_USER', 'id')
    if RESTRICT_BY_USER:
        try:
            user_prop = getattr(user, RESTRICT_BY_USER)
        except (AttributeError, TypeError):
            user_prop = getattr(user, 'get_username')

        if callable(user_prop):
            user_path = user_prop()
        else:
            user_path = user_prop

    return str(user_path)


def get_upload_filename(upload_name, request):
    user_path = _get_user_path(request.user)

    # Generate date based path to put uploaded file.
    # If CKEDITOR_RESTRICT_BY_DATE is True upload file to date specific path -> Only year (for browse performance)
    if getattr(settings, 'CKEDITOR_RESTRICT_BY_DATE', True):
        date_path = datetime.now().strftime('%Y')
    else:
        date_path = ''

    # Complete upload path (upload_path + date_path).
    upload_path = os.path.join(
        settings.CKEDITOR_UPLOAD_PATH, user_path, date_path
    )

    if (getattr(settings, 'CKEDITOR_UPLOAD_SLUGIFY_FILENAME', True) and
            not hasattr(settings, 'CKEDITOR_FILENAME_GENERATOR')):
        upload_name = utils.slugify_filename(upload_name)

    if hasattr(settings, 'CKEDITOR_FILENAME_GENERATOR'):
        generator = import_string(settings.CKEDITOR_FILENAME_GENERATOR)
        # Does the generator accept a request argument?
        try:
            inspect.getcallargs(generator, upload_name, request)
        except TypeError:
            # Does the generator accept only an upload_name argument?
            try:
                inspect.getcallargs(generator, upload_name)
            except TypeError:
                warnings.warn(
                    "Update %s() to accept the arguments `filename, request`."
                    % settings.CKEDITOR_FILENAME_GENERATOR
                )
            else:
                warnings.warn(
                    "Update %s() to accept a second `request` argument."
                    % settings.CKEDITOR_FILENAME_GENERATOR,
                    PendingDeprecationWarning
                )
                upload_name = generator(upload_name)
        else:
            upload_name = generator(upload_name, request)

    return storage.get_available_name(
        os.path.join(upload_path, upload_name)
    )


class ImageUploadView(generic.View):
    http_method_names = ['post']

    def post(self, request, **kwargs):
        """
        Uploads a file and send back its URL to CKEditor.
        """
        uploaded_file = request.FILES['upload']

        backend = registry.get_backend()

        ck_func_num = request.GET.get('CKEditorFuncNum')
        if ck_func_num:
            ck_func_num = escape(ck_func_num)

        filewrapper = backend(storage, uploaded_file)
        allow_nonimages = getattr(settings, 'CKEDITOR_ALLOW_NONIMAGE_FILES', True)
        # Throws an error when an non-image file are uploaded.
        if not filewrapper.is_image and not allow_nonimages:
            return HttpResponse("""
                <script type='text/javascript'>
                window.parent.CKEDITOR.tools.callFunction({0}, '', 'Invalid file type.');
                </script>""".format(ck_func_num))

        filepath = get_upload_filename(uploaded_file.name, request)

        saved_path = filewrapper.save_as(filepath)

        url = utils.get_media_url(saved_path)

        if ck_func_num:
            # Respond with Javascript sending ckeditor upload url.
            return HttpResponse("""
            <script type='text/javascript'>
                window.parent.CKEDITOR.tools.callFunction({0}, '{1}');
            </script>""".format(ck_func_num, url))
        else:
            _, filename = os.path.split(saved_path)
            retdata = {'url': url, 'uploaded': '1',
                       'fileName': filename}
            return JsonResponse(retdata)


upload = csrf_exempt(ImageUploadView.as_view())


def get_image_files(user=None, path=''):
    """
    Recursively walks all dirs under upload dir and generates a list of
    full paths for each file found.
    """
    # If a user is provided and CKEDITOR_RESTRICT_BY_USER is True,
    # limit images to user specific path
    STORAGE_DIRECTORIES = 0
    STORAGE_FILES = 1

    # use the user path
    user_path = _get_user_path(user)

    # Security: do not allow user to see all files
    if not user_path or user_path == '':
        logger.error('User path is empty. Impossible to show files')
        return

    browse_path = os.path.join(settings.CKEDITOR_UPLOAD_PATH, user_path, path)

    try:
        storage_list = storage.listdir(browse_path)
    except NotImplementedError:
        return
    except OSError:
        return

    for filename in storage_list[STORAGE_FILES]:
        if os.path.splitext(filename)[0].endswith('_thumb') or os.path.basename(filename).startswith('.'):
            continue
        filename = os.path.join(browse_path, filename)
        yield filename

    for directory in storage_list[STORAGE_DIRECTORIES]:
        if directory.startswith('.'):
            continue
        directory_path = os.path.join(path, directory)
        for element in get_image_files(user=user, path=directory_path):
            yield element


class FileDeleteView(generic.View):
    http_method_names = ['delete']

    def delete(self, request, **kwargs):
        try:
            # Check if user is authenticated
            if request.user and request.user.is_authenticated:
                file_to_be_deleted = request.GET['path']
                full_user_path = os.path.join(settings.CKEDITOR_UPLOAD_PATH, _get_user_path(request.user)) + '/'
                logger.info('User want to delete %s with user path %s' % (file_to_be_deleted, full_user_path))

                # Secutiry: check if the file is owned by user.
                if full_user_path and full_user_path in file_to_be_deleted:
                    if is_valid_image_extension(file_to_be_deleted):
                        thumbnail_filename_path = utils.get_thumb_filename(file_to_be_deleted)
                        storage.delete(thumbnail_filename_path)

                    storage.delete(file_to_be_deleted)
                    return JsonResponse({'success': 1})
        except Exception as error:
            pass

        return JsonResponse(data={'success': 0}, status=403)


delete = FileDeleteView.as_view()


def get_files_browse_urls(user=None):
    """
    Recursively walks all dirs under upload dir and generates a list of
    thumbnail and full image URL's for each file found.
    """
    files = []
    for filename in get_image_files(user=user):
        src = utils.get_media_url(filename)
        if getattr(settings, 'CKEDITOR_IMAGE_BACKEND', None):
            if is_valid_image_extension(src):
                thumb = utils.get_media_url(utils.get_thumb_filename(filename))
            else:
                thumb = utils.get_icon_filename(filename)
            visible_filename = os.path.split(filename)[1]
            if len(visible_filename) > 30:
                visible_filename = visible_filename[0:29] + "..."
        else:
            thumb = src
            visible_filename = os.path.split(filename)[1]

        temp, extension = os.path.splitext(filename)

        files.append(
            {
                'thumb': thumb,
                'src': src,
                'path': filename,
                'is_image': is_valid_image_extension(src),
                'is_video': is_valid_video_extension(src),
                'visible_filename': visible_filename,
                'extension': extension,
            }
        )

    return files


def browse(request):
    files = get_files_browse_urls(request.user)
    if request.method == 'POST':
        form = SearchForm(request.POST)
        if form.is_valid():
            query = form.cleaned_data.get('q', '').lower()
            files = list(filter(lambda d: query in d['path'].lower(), files))
    else:
        form = SearchForm()

    # Filter by type file
    if 'type' in request.GET:
        typeOfFile = request.GET['type']
        if typeOfFile == "video":
            files = list(filter(lambda d: d['is_video'], files))
        if typeOfFile == "image":
            files = list(filter(lambda d: d['is_image'], files))

    show_dirs = getattr(settings, 'CKEDITOR_BROWSE_SHOW_DIRS', False)
    dir_list = sorted(set(os.path.dirname(f['src'])
                          for f in files), reverse=True)

    # Ensures there are no objects created from Thumbs.db files - ran across
    # this problem while developing on Windows
    if os.name == 'nt':
        files = [f for f in files if os.path.basename(f['src']) != 'Thumbs.db']

    context = {
        'show_dirs': show_dirs,
        'dirs': dir_list,
        'files': files,
        'form': form
    }
    return render(request, 'ckeditor/browse.html', context)


def browseAllFiles(request):
    files = get_files_browse_urls(request.user)
    if request.method == 'POST':
        form = SearchForm(request.POST)
        if form.is_valid():
            query = form.cleaned_data.get('q', '').lower()
            files = list(filter(lambda d: query in d['path'].lower(), files))
    else:
        form = SearchForm()

    # Filter by type file
    if 'type' in request.GET:
        typeOfFile = request.GET['type']
        if typeOfFile == "video":
            files = list(filter(lambda d: d['is_video'], files))
        if typeOfFile == "image":
            files = list(filter(lambda d: d['is_image'], files))

    show_dirs = getattr(settings, 'CKEDITOR_BROWSE_SHOW_DIRS', False)
    dir_list = sorted(set(os.path.dirname(f['src'])
                          for f in files), reverse=True)

    # Ensures there are no objects created from Thumbs.db files - ran across
    # this problem while developing on Windows
    if os.name == 'nt':
        files = [f for f in files if os.path.basename(f['src']) != 'Thumbs.db']

    context = {
        'show_dirs': show_dirs,
        'dirs': dir_list,
        'files': files,
        'form': form
    }
    return render(request, 'ckeditor/browseAllFiles.html', context)


def browseImages(request):
    files = get_files_browse_urls(request.user)
    if request.method == 'POST':
        form = SearchForm(request.POST)
        if form.is_valid():
            query = form.cleaned_data.get('q', '').lower()
            files = list(filter(lambda d: query in d['path'].lower(), files))
    else:
        form = SearchForm()

    # Filter by type file
    files = list(filter(lambda d: d['is_image'], files))

    show_dirs = getattr(settings, 'CKEDITOR_BROWSE_SHOW_DIRS', False)
    dir_list = sorted(set(os.path.dirname(f['src'])
                          for f in files), reverse=True)

    # Ensures there are no objects created from Thumbs.db files - ran across
    # this problem while developing on Windows
    if os.name == 'nt':
        files = [f for f in files if os.path.basename(f['src']) != 'Thumbs.db']

    context = {
        'show_dirs': show_dirs,
        'dirs': dir_list,
        'files': files,
        'form': form
    }
    return render(request, 'ckeditor/browseImages.html', context)
