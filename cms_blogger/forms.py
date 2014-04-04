from django import forms
from django.core.exceptions import ValidationError
from django.conf import settings
from django.template.defaultfilters import slugify
from django.forms.util import ErrorList
from django.contrib.contenttypes.generic import BaseGenericInlineFormSet
from django.contrib.sites.models import Site
from django.utils.translation import ugettext_lazy as _
from django.utils import timezone
from django.contrib.auth.models import User
from cms.plugin_pool import plugin_pool
from cms.plugins.text.settings import USE_TINYMCE
from cms.plugins.text.widgets.wymeditor_widget import WYMEditor
from cms.utils.plugins import get_placeholders
from cms.models import Page
from cms_layouts.models import Layout
from cms_layouts.slot_finder import (
    get_fixed_section_slots, MissingRequiredPlaceholder)
from django_select2.fields import (
    AutoModelSelect2Field, AutoModelSelect2MultipleField)
from .models import Blog, BlogEntryPage, BlogCategory
from .widgets import TagItWidget
from .utils import user_display_name


class BlogLayoutInlineFormSet(BaseGenericInlineFormSet):

    def clean(self):
        if any(self.errors):
            return
        data = self.cleaned_data
        data_to_delete = filter(lambda x: x.get('DELETE', False), data)
        data = filter(lambda x: not x.get('DELETE', False), data)

        if len(data) < 1:
            raise ValidationError('At least one layout is required!')

        if len(data) > len(Blog.LAYOUTS_CHOICES):
            raise ValidationError(
                'There can be a maximum of %d layouts.' % \
                    len(Blog.LAYOUTS_CHOICES))

        submitted_layout_types = [layout.get('layout_type')
                                  for layout in data]

        if len(submitted_layout_types) != len(set(submitted_layout_types)):
            raise ValidationError(
                "You can have only one layout for each layout type.")

        specific_layout_types = [layout_type
            for layout_type in Blog.LAYOUTS_CHOICES.keys()
            if layout_type != Blog.ALL]

        if Blog.ALL not in submitted_layout_types:
            # check if there are layouts for all of the rest types
            if not all([specific_layout_type in submitted_layout_types
                        for specific_layout_type in specific_layout_types]):
                pretty_specific_layout_types = (
                    Blog.LAYOUTS_CHOICES[layout_type]
                    for layout_type in specific_layout_types)
                raise ValidationError(
                    "If you do not have a layout for %s you need to specify "
                    "a layout for all the rest layout types: %s" % (
                        Blog.LAYOUTS_CHOICES[Blog.ALL],
                        ', '.join(pretty_specific_layout_types)))


class BlogLayoutForm(forms.ModelForm):
    layout_type = forms.IntegerField(
        label='Layout Type',
        widget=forms.Select(choices=Blog.LAYOUTS_CHOICES.items()))
    from_page = forms.IntegerField(
        label='Inheriting layout from page', widget=forms.Select())

    class Meta:
        model = Layout
        fields = ('layout_type', 'from_page')

    def clean_layout_type(self):
        layout_type = self.cleaned_data.get('layout_type', None)
        if layout_type == None:
            raise ValidationError("Layout Type required")
        if layout_type not in Blog.LAYOUTS_CHOICES.keys():
            raise ValidationError(
                "Not a valid Layout Type. Valid choices are: %s" % (
                    ', '.join(Blog.LAYOUTS_CHOICES.values())))
        return layout_type

    def clean_from_page(self):
        from_page_id = self.cleaned_data.get('from_page', None)
        if not from_page_id:
            raise ValidationError('Select a page for this layout.')
        try:
            page = Page.objects.get(id=from_page_id)
        except Page.DoesNotExist:
            raise ValidationError(
                'This page does not exist. Refresh this form and select an '
                'existing page.')
        try:
            slots = get_placeholders(page.get_template())
            fixed_slots = get_fixed_section_slots(slots)
            return page
        except MissingRequiredPlaceholder, e:
            raise ValidationError(
                "Page %s is missing a required placeholder "
                "named %s. Choose a different page for this layout that"
                " has the required placeholder or just add this "
                "placeholder in the page template." % (page, e.slot, ))
        except Exception, page_exception:
            raise ValidationError(
                "Error found while scanning template from page %s: %s. "
                "Change page with a valid one or fix this error." % (
                    page, page_exception))


class BlogUserField(AutoModelSelect2MultipleField):
    search_fields = ['first_name__icontains', 'last_name__icontains',
                     'email__icontains', 'username__icontains']
    queryset = User.objects.all()
    empty_values = [None, '', 0]

    def label_from_instance(self, obj):
        return user_display_name(obj)


class BlogForm(forms.ModelForm):
    categories = forms.CharField(
        widget=TagItWidget(attrs={
            'tagit': '{allowSpaces: true, tagLimit: 20, caseSensitive: false}'}),
        help_text=_('Categories help text'))

    allowed_users = BlogUserField(label="Add Users")

    class Meta:
        model = Blog

    def __init__(self, *args, **kwargs):
        super(BlogForm, self).__init__(*args, **kwargs)
        if (self.instance and 'categories' in self.fields and
                not self.fields['categories'].initial):
            self.fields['categories'].initial = ', '.join(
                self.instance.categories.values_list('name', flat=True))
        if (not self.is_bound and self.instance and self.instance.pk and
                self.instance.layouts.count() == 0):
            self.missing_layouts = ErrorList([
                "This blog is missing a layout. "
                "Add one in the Layouts section."])
        else:
            self.missing_layouts = False

    def clean_categories(self):
        categories = self.cleaned_data.get('categories', '')
        if not categories:
            raise ValidationError("Add at least one category.")

        categories_names = [name.strip().lower()
                            for name in categories.split(',')]

        if len(categories_names) != len(set(categories_names)):
            raise ValidationError(
                "Category names not unique.")
        blog = self.instance

        existing_names = dict([(categ.name, categ)
                               for categ in blog.categories.all()])
        category_objs = []
        for name in categories_names:
            if name not in existing_names:
                category = BlogCategory()
                category.name = name
            else:
                category = existing_names[name]
            category_objs.append(category)
        return category_objs

    def clean_in_navigation(self):
        in_navigation = self.cleaned_data.get('in_navigation', False)
        if in_navigation:
            if not self.instance.navigation_node:
                raise ValidationError(
                    "Choose a location in the navigation menu")
        return in_navigation

    def clean_slug(self):
        return slugify(self.cleaned_data.get('slug', ''))

    def clean_disqus_shortname(self):
        disqus_enabled = self.cleaned_data.get('enable_disqus', None)
        disqus_shortname = self.cleaned_data.get('disqus_shortname', None)
        if disqus_enabled and not disqus_shortname:
            raise ValidationError('Disqus shortname required.')
        return disqus_shortname


class BlogAddForm(forms.ModelForm):

    class Meta:
        model = Blog
        fields = ('title', 'slug', 'site')


class BlogEntryPageAddForm(forms.ModelForm):

    class Meta:
        model = BlogEntryPage
        fields = ('blog', )


def _get_text_editor_widget():
    installed_plugins = plugin_pool.get_all_plugins()
    plugins = [plugin for plugin in installed_plugins if plugin.text_enabled]

    if USE_TINYMCE and "tinymce" in settings.INSTALLED_APPS:
        from cms.plugins.text.widgets.tinymce_widget import TinyMCEEditor
        return TinyMCEEditor(installed_plugins=plugins)
    else:
        return WYMEditor(installed_plugins=plugins)


class AuthorField(AutoModelSelect2Field):

    search_fields = ['first_name__icontains', 'last_name__icontains',
                     'email__icontains', 'username__icontains']
    queryset = User.objects.all()
    empty_values = [None, '', 0]

    def label_from_instance(self, obj):
        return user_display_name(obj)


class BlogEntryPageChangeForm(forms.ModelForm):
    body = forms.CharField(
        label='Blog Entry', required=True,
        widget=_get_text_editor_widget())
    author = AuthorField()
    categories = forms.ModelMultipleChoiceField(
        widget=forms.CheckboxSelectMultiple(),
        queryset=BlogCategory.objects.get_empty_query_set())


    class Media:
        css = {"all": ("cms_blogger/css/entry-change-form.css", )}

    class Meta:
        model = BlogEntryPage
        exclude = ('content', 'blog', 'slug', 'publication_date')

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        categories_field = self.base_fields.get('categories')
        if categories_field and instance and instance.blog:
            categories_field.queryset = instance.blog.categories.all()
            categories_field.initial = instance.categories.all()
        super(BlogEntryPageChangeForm, self).__init__(*args, **kwargs)
        self.fields['body'].initial = self.instance.content_body
        # prepare for save
        self.instance.draft_id = None

    def clean_body(self):
        body = self.cleaned_data.get('body')
        self.instance.content_body = body
        return body

    def clean_title(self):
        title = self.cleaned_data.get('title', '')
        slug = slugify(title)
        blog_id = self.instance.blog_id
        try:
            BlogEntryPage.objects.exclude(pk=self.instance.pk).get(
                slug=slug, blog=blog_id, draft_id=None)
        except BlogEntryPage.DoesNotExist:
            pass
        else:
            raise ValidationError(
                "Entry with slug %s already exists. Choose a different "
                "title." % slug)
        self.instance.slug = slug
        return title

    def _reset_publication_date(self):
        is_published = self.cleaned_data.get('is_published')
        start_date = self.cleaned_data.get('start_publication')
        # check if reset publication date is needed
        when = now = timezone.now()
        if is_published:
            if not self.instance.is_published:
                # if however a startdate was set
                if start_date and not self.instance.start_publication:
                    when = start_date
                self.instance.publication_date = when
            elif start_date != self.instance.start_publication:
                # if was published but start pub date changed
                self.instance.publication_date = start_date or now
        else:
            self.cleaned_data['start_publication'] = None
            self.cleaned_data['end_publication'] = None
            self.instance.publication_date = now

    def clean(self):
        self._reset_publication_date()
        start_date = self.cleaned_data.get('start_publication')
        end_date = self.cleaned_data.get('end_publication')
        if (start_date and end_date and not start_date < end_date):
            raise ValidationError("Incorrect publication dates interval.")
        return self.cleaned_data

