angular.module("joinForm", []);

function JoinCtrl($scope) {
    $scope._mode = /^\/update/.test(location.pathname) ? "update" : "new";
    $scope._title = $scope.mode == "new" ? "New Member" : "Membership Update";
    $scope.submit = function() {
        var prop, node,
            out = {};
        
        $(".ng-pristine").removeClass("ng-pristine").addClass("ng-dirty");

        for (prop in $scope) {
            if (/^[\$\_]/.test(prop) || prop == "isRequired" || prop == "this" || prop == "submit") {
                continue;
            }
            out[prop] = $scope[prop];
        }

        node = $("input.ng-invalid-required:visible").first();
        if (node.length) {
            node.focus();
        } else {
            // Do ajax
            $.ajax(location.pathname, {
                method: "post",
                data: {"data": JSON.stringify(out)},
            }).fail(function() {
                $("#completed").find(".alert").removeClass('alert-info').addClass('alert-danger')
                    .html("For some reason, the form had an error. Please contact the <a href='mailto:membership@pirateparty.org.au'>Secretary</a> for assistance.")
            }).always(function(data) {
                $("form").addClass('hidden');
                $("#completed").removeClass('hidden');
            });
        }
    }
    $scope.isRequired = function() {
        console.log(arguments);
        var visible = $(this).is(":visible"),
            id = $(this).attr('id');
        
        if (visible) {
            if (id) {
                $("[for='" + id + "']").append("<span style='color: red'> *</span>");
            } else {
                $(this).parent().append("<span style='color: red'> *</span>");
            }
        }

        return visible;
    };
    
    $scope.membership_level = "full";
    $scope.payment_method = "paypal";
    $scope._states = ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"];

    if ($scope._mode == "new") {
        $scope._msg = "Your application was submitted successfully. You should receive an email confirming the receipt of your application and an invoice in the next 10 minutes.<br><br>If you don't receive that email, please contact <a href='mailto:membership@pirateparty.org.au'>membership@pirateparty.org.au</a>.";
    } else {
        $scope._msg = "Your information has been successfully updated. Thanks!";
    }
}
